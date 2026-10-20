"""Rev-O-mate のボタン用スクリプト（マクロ）の保存場所を、公式アプリと同じ決まりで並べ直して書くための部品。

公式アプリ（FlashControl.cs / Form1.cs）の決まり:
    0x10000〜  スクリプト情報の表（セクタ 1）
                 [0..1] チェックサム（未使用 ff ff）  [2] 記録スクリプト数  [4..7] 全スクリプトの合計バイト数（リトルエンディアン）
                 0x10 + 0x110*(番号-1) ごとに 1 件:
                     [0..3] データのアドレス  [4..7] 大きさ  [8] モード  [9] 名前の長さ  [10..] 名前（UTF-16LE）
    0x20000〜  スクリプトのデータ（セクタ 2〜31）。1 つのセクタに、複数のスクリプトを、番号順に、隙間なく詰める。
                 1 つのスクリプトは最大 0x2492 (9362) バイト、1 セクタに最大 7 個、全体で最大 200 個。
    スクリプトの大きさを変えるときは、そのセクタの中のスクリプトを並べ直して書き、
    表の中のアドレスと大きさ、記録数、合計バイト数を直す。

この部品は「計画を作る」だけです（本体には何も書きません）。書き込みは、呼び出し側が、
バックアップを取ってから、`plan["sector_index"]`（データ）→ `1`（表）の順で行います。
"""

INFO_ADDR = 0x10000
SECTOR_SIZE = 0x10000
REC_SIZE = 0x110
HEADER_SIZE = 0x10
MAX_SCRIPTS = 200
DATA_FIRST_SECTOR, DATA_LAST_SECTOR = 2, 31
MAX_SCRIPT_BYTES = 0x2492
MAX_PER_SECTOR = 7


class ScriptError(ValueError):
    pass


def _rec_off(n):
    return HEADER_SIZE + REC_SIZE * (n - 1)


def read_records(info):
    """表（セクタ 1 の全バイト）から、使われているスクリプトの一覧を作る。{番号: {addr, size}}"""
    if len(info) != SECTOR_SIZE:
        raise ScriptError("スクリプト情報の大きさが正しくありません")
    out = {}
    for n in range(1, MAX_SCRIPTS + 1):
        o = _rec_off(n)
        addr = int.from_bytes(info[o:o + 4], "little")
        size = int.from_bytes(info[o + 4:o + 8], "little")
        if size in (0, 0xFFFFFFFF) or addr in (0, 0xFFFFFFFF):
            continue
        out[n] = {"addr": addr, "size": size, "mode": info[o + 8]}
    return out


def sector_group(records, sector):
    """そのセクタに入っているスクリプトの番号を、アドレス順に返す。"""
    nums = [n for n, r in records.items() if r["addr"] // SECTOR_SIZE == sector]
    return sorted(nums, key=lambda n: records[n]["addr"])


def check_table(info, flash_read):
    """表が、公式の決まりどおりか確かめる（問題の一覧を返す。空なら大丈夫）。"""
    problems = []
    recs = read_records(info)
    if info[2] != len(recs):
        problems.append(f"記録数 {info[2]} と、使われている件数 {len(recs)} が違います")
    total = int.from_bytes(info[4:8], "little")
    if total != sum(r["size"] for r in recs.values()):
        problems.append(f"合計 {total} と、大きさの合計が違います")
    for sector in {r["addr"] // SECTOR_SIZE for r in recs.values()}:
        if not (DATA_FIRST_SECTOR <= sector <= DATA_LAST_SECTOR):
            problems.append(f"セクタ {sector} は、スクリプトの置き場の外です")
            continue
        pos = 0
        for n in sector_group(recs, sector):
            r = recs[n]
            if r["addr"] % SECTOR_SIZE != pos:
                problems.append(f"スクリプト {n} が、前のスクリプトの直後にありません")
            pos = r["addr"] % SECTOR_SIZE + r["size"]
            if r["size"] > MAX_SCRIPT_BYTES:
                problems.append(f"スクリプト {n} が大きすぎます")
        if len(sector_group(recs, sector)) > MAX_PER_SECTOR:
            problems.append(f"セクタ {sector} に、7 個より多く入っています")
    return problems


def plan_replace(info, flash_read, number, new_body, mode=None):
    """既にあるスクリプト `number` の中身を `new_body` に差し替える計画（大きさが変わってもよい）。

    flash_read(addr, size) -> bytes。返り値の辞書:
        sector_index   データを書くセクタ番号      sector_data  そのセクタの新しい全バイト
        info_data      表（セクタ 1）の新しい全バイト
        moved          [(番号, 前のアドレス, 前の大きさ, 新しいアドレス, 新しい大きさ), ...]（変わるものだけ）
    """
    recs = read_records(info)
    if number not in recs:
        raise ScriptError(f"スクリプト {number} は、まだありません（新しく作る機能は、別です）")
    if not (1 <= len(new_body) <= MAX_SCRIPT_BYTES):
        raise ScriptError(f"スクリプトの大きさは 1〜{MAX_SCRIPT_BYTES} バイトにしてください（{len(new_body)}）")
    sector = recs[number]["addr"] // SECTOR_SIZE
    if not (DATA_FIRST_SECTOR <= sector <= DATA_LAST_SECTOR):
        raise ScriptError("スクリプトの置き場の外にあります")
    group = sector_group(recs, sector)

    packed = bytearray()
    new_pos = {}
    for n in group:
        body = bytes(new_body) if n == number else bytes(flash_read(recs[n]["addr"], recs[n]["size"]))
        if len(body) != (len(new_body) if n == number else recs[n]["size"]):
            raise ScriptError(f"スクリプト {n} を読み込めませんでした")
        new_pos[n] = (sector * SECTOR_SIZE + len(packed), len(body))
        packed += body
    if len(packed) > SECTOR_SIZE:
        raise ScriptError(f"このセクタに入りきりません（{len(packed)} > {SECTOR_SIZE} バイト）")

    sector_data = bytes(packed) + b"\xFF" * (SECTOR_SIZE - len(packed))

    new_info = bytearray(info)
    moved = []
    for n in group:
        addr, size = new_pos[n]
        if (addr, size) != (recs[n]["addr"], recs[n]["size"]):
            o = _rec_off(n)
            new_info[o:o + 4] = addr.to_bytes(4, "little")
            new_info[o + 4:o + 8] = size.to_bytes(4, "little")
            moved.append((n, recs[n]["addr"], recs[n]["size"], addr, size))
    # 記録数と合計バイト数（公式と同じ数え方: 大きさが 0 より大きいものだけ）
    if mode is not None:
        new_info[_rec_off(number) + 8] = mode & 0xFF     # スクリプトのモード（0=1回, 1=繰り返し, 2=押している間くり返す, 3=ホールド）
    sizes = {n: (new_pos[n][1] if n in new_pos else r["size"]) for n, r in recs.items()}
    new_info[2] = len(sizes)
    new_info[4:8] = sum(sizes.values()).to_bytes(4, "little")
    return {"sector_index": sector, "sector_data": sector_data, "info_data": bytes(new_info), "moved": moved}


def button_script_number(mode, sw_index):
    """公式の出荷時の番号の付け方: モード m（0 始まり）のボタン sw_index（0 始まり）は、150 + 11*m + (sw_index+1)。"""
    return 150 + 11 * mode + sw_index + 1


def pick_free_number(records, preferred):
    """まだ使われていない番号を返す。希望の番号が空ならそれ、だめなら 151〜200 の最初の空き。なければ None。"""
    if preferred not in records and 1 <= preferred <= MAX_SCRIPTS:
        return preferred
    for n in range(151, MAX_SCRIPTS + 1):
        if n not in records:
            return n
    return None


def _choose_sector(records, number, size):
    """新しいスクリプトを置くセクタを決める。入る（7 個まで・65536 バイトまで）うち、他を動かさずに済むセクタを優先。"""
    best = None
    for sector in range(DATA_FIRST_SECTOR, DATA_LAST_SECTOR + 1):
        group = sector_group(records, sector)
        if len(group) >= MAX_PER_SECTOR:
            continue
        used = sum(records[n]["size"] for n in group)
        if used + size > SECTOR_SIZE:
            continue
        shifts = sum(1 for n in group if n > number)      # 番号が大きいものは、うしろへずれる
        key = (shifts, 0 if group else 1, sector)         # ずれが少ない → 既に使っているセクタ → 若い番号
        if best is None or key < best[0]:
            best = (key, sector)
    return None if best is None else best[1]


def plan_insert(info, flash_read, number, new_body, mode=0):
    """まだ存在しないスクリプト `number` を、新しく作る計画（plan_replace と同じ形の辞書を返す）。"""
    recs = read_records(info)
    if number in recs:
        raise ScriptError(f"スクリプト {number} は、すでにあります")
    if not (1 <= number <= MAX_SCRIPTS):
        raise ScriptError("スクリプト番号は 1〜200 です")
    if not (1 <= len(new_body) <= MAX_SCRIPT_BYTES):
        raise ScriptError(f"スクリプトの大きさは 1〜{MAX_SCRIPT_BYTES} バイトにしてください（{len(new_body)}）")
    sector = _choose_sector(recs, number, len(new_body))
    if sector is None:
        raise ScriptError("新しいスクリプトを置く場所がありません")
    group = sorted(sector_group(recs, sector) + [number])

    packed = bytearray()
    new_pos = {}
    for n in group:
        body = bytes(new_body) if n == number else bytes(flash_read(recs[n]["addr"], recs[n]["size"]))
        if n != number and len(body) != recs[n]["size"]:
            raise ScriptError(f"スクリプト {n} を読み込めませんでした")
        new_pos[n] = (sector * SECTOR_SIZE + len(packed), len(body))
        packed += body
    sector_data = bytes(packed) + b"\xFF" * (SECTOR_SIZE - len(packed))

    new_info = bytearray(info)
    moved = []
    for n in group:
        addr, size = new_pos[n]
        o = _rec_off(n)
        if n == number:
            new_info[o:o + REC_SIZE] = b"\xFF" * REC_SIZE      # 空の記録を作り直す（公式の未使用の記録と同じ初期値）
            new_info[o:o + 4] = addr.to_bytes(4, "little")
            new_info[o + 4:o + 8] = size.to_bytes(4, "little")
            new_info[o + 8] = mode & 0xFF                      # モード
            new_info[o + 9] = 0                                # 名前の長さ
            moved.append((n, 0, 0, addr, size))
        elif (addr, size) != (recs[n]["addr"], recs[n]["size"]):
            new_info[o:o + 4] = addr.to_bytes(4, "little")
            new_info[o + 4:o + 8] = size.to_bytes(4, "little")
            moved.append((n, recs[n]["addr"], recs[n]["size"], addr, size))
    sizes = {n: r["size"] for n, r in recs.items()}
    sizes[number] = len(new_body)
    new_info[2] = len(sizes)
    new_info[4:8] = sum(sizes.values()).to_bytes(4, "little")
    return {"sector_index": sector, "sector_data": sector_data, "info_data": bytes(new_info), "moved": moved}
