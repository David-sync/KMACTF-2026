#!/usr/bin/env python3
import re
import struct

from pwn import context, remote

from purevenj_game_core import ADJ, GameCore, stage_params

HOST = "67.223.119.69"
PORT = 3649
CHAL = "./chal"


# Output gọn để dùng khi solve/writeup:
# - current: vị trí người chơi đang bám
# - active : piece thật của binary, lấy bằng Fmem qua GameCore.is_member
# - focus  : trục xoay, chỉ quan trọng khi rw/rrw
SHOW_FULL_FLAGS = False


def norm_cmd(s: str) -> str:
    return " ".join(s.strip().lower().split())


def build_stages(seed: int):
    stages = []
    cur = seed
    for i in range(3):
        next_seed, node, slot, t1, t2 = stage_params(cur)
        stages.append(
            {
                "idx": i + 1,
                "stage_seed": cur,
                "next_seed": next_seed,
                "node": node,
                "slot": slot,
                "edge_to": ADJ[node][slot],
                "max_moves": 13 + 3 * i,
                "t1": t1,
                "t2": t2,
            }
        )
        cur = next_seed
    return stages


def init_local_state(game: GameCore, stage_info):
    return game.init_stage(
        stage_info["idx"] - 1,
        stage_info["node"],
        stage_info["slot"],
        stage_info["t1"],
        stage_info["t2"],
    )


def show_stage(stage_info):
    print()
    print(f"[stage {stage_info['idx']}/3]")
    print(f"seed  = {stage_info['stage_seed']:#x}")
    print(
        f"start = node {stage_info['node']}, slot {stage_info['slot']}  "
        f"({stage_info['node']}->{stage_info['edge_to']})"
    )
    print(f"need  = C({stage_info['t1']}) -> C({stage_info['t2']}) -> C(0)")
    print(f"limit = {stage_info['max_moves']} moves")


def k_face(tri):
    _, v, i = tri
    return (v, ADJ[v][(i - 1) % 5], ADJ[v][i])


def e_to(tri):
    _, v, i = tri
    return ADJ[v][i]


def all_members(game: GameCore, st):
    """Enumerate cells that binary says belong to the current active piece."""
    out = []
    for typ in (1, 2):  # E/K are the useful active cells for this challenge.
        for v in range(12):
            for i in range(5):
                tri = (typ, v, i)
                if game.is_member(st, tri):
                    out.append(tri)
    return out


def active_desc(game: GameCore, st, stage_info):
    members = all_members(game, st)
    es = [x for x in members if x[0] == 1]
    ks = [x for x in members if x[0] == 2]
    stage = stage_info["idx"]

    if stage == 1:
        if not ks:
            return "face/K: <none>"
        face = k_face(ks[0])
        cells = ", ".join(game.name(x) for x in ks)
        return f"face {face} ; {cells}"

    if stage == 2:
        if not es:
            return "edge/E: <none>"
        v, u = es[0][1], e_to(es[0])
        cells = ", ".join(game.name(x) for x in es)
        return f"edge {v}--{u} ; {cells}"

    # Stage 3/tag 4: usually a directed E cell. Keep output compact.
    parts = []
    for tri in es:
        v, i = tri[1], tri[2]
        parts.append(f"{game.name(tri)}={v}->{ADJ[v][i]}")
    for tri in ks:
        parts.append(f"{game.name(tri)}=face{k_face(tri)}")
    return " ; ".join(parts) if parts else "mixed: <none>"


def next_goal(game: GameCore, st, stage_info):
    ret0, hit1, hit2 = game.flags(st)
    if not hit1:
        return f"C({stage_info['t1']})"
    if not hit2:
        return f"C({stage_info['t2']})"
    if not ret0:
        return "C(0)"
    return "clear"


def show_state(game: GameCore, local_state, stage_info):
    ret0, hit1, hit2 = game.flags(local_state)

    print()
    print("[state]")
    print(f"stage   : {stage_info['idx']}/3")
    print(f"current : {game.name(game.cur(local_state))}")
    print(f"active  : {active_desc(game, local_state, stage_info)}")
    print(f"focus   : {game.focus(local_state)}")
    print(f"moves   : {game.moves(local_state)}/{game.maxmoves(local_state)}")
    print(f"next    : {next_goal(game, local_state, stage_info)}")
    if SHOW_FULL_FLAGS:
        print(f"flags   : return0={ret0} target1={hit1} target2={hit2}")
        print(f"need    : C({stage_info['t1']}) -> C({stage_info['t2']}) -> C(0)")
    print()


def get_seed(io):
    banner = io.recvuntil(b"> ")
    print(banner.decode(errors="replace"), end="")

    io.sendline(b"hint")
    io.recvuntil(b"Khong de the dau hehe: ")
    seed = struct.unpack("<Q", io.recvn(8))[0]

    # Consume the prompt after hint.
    io.recvuntil(b"> ")
    return seed


def recv_after_command(io):
    try:
        data = io.recvuntil(b"> ", timeout=2)
    except EOFError:
        data = io.recvall(timeout=1)

    if not data:
        data = io.recvall(timeout=1)

    return data


def main():
    context.log_level = "error"

    io = remote(HOST, PORT)
    game = GameCore(CHAL)

    seed = get_seed(io)
    stages = build_stages(seed)

    stage_idx = 1
    stage_info = stages[stage_idx - 1]
    local_state = init_local_state(game, stage_info)

    show_stage(stage_info)
    show_state(game, local_state, stage_info)

    while True:
        cmd = norm_cmd(input("manual> "))

        if not cmd:
            continue

        if cmd in {"exit", "quit"}:
            break

        if cmd == "state":
            show_state(game, local_state, stage_info)
            continue

        if cmd == "full":
            global SHOW_FULL_FLAGS
            SHOW_FULL_FLAGS = not SHOW_FULL_FLAGS
            print(f"[local] full output = {SHOW_FULL_FLAGS}")
            show_state(game, local_state, stage_info)
            continue

        # q là command thật của challenge, gửi lên remote rồi thoát.
        if cmd == "q":
            io.sendline(b"q")
            print(io.recvall(timeout=1).decode(errors="replace"))
            break

        # Mirror local bằng binary core để biết current/focus/moves chính xác.
        legal_map = dict(game.legal(local_state))

        # Không in list legal cho đỡ rối; chỉ chặn lệnh sai để không chết session.
        if cmd not in legal_map:
            print()
            print(f"[local] không gửi: '{cmd}' không hợp lệ từ state hiện tại")
            print(
                f"        current={game.name(game.cur(local_state))}, "
                f"active={active_desc(game, local_state, stage_info)}, "
                f"focus={game.focus(local_state)}, "
                f"moves={game.moves(local_state)}/{game.maxmoves(local_state)}"
            )
            print("        gõ 'state' để xem lại trạng thái, gõ 'full' để bật/tắt output đầy đủ")
            print()
            continue

        local_state = legal_map[cmd]
        io.sendline(cmd.encode())

        out = recv_after_command(io)
        text = out.decode(errors="replace")
        print(text, end="")

        # Nếu server mở stage mới, reset mirror local sang stage đó.
        match = re.search(r"Stage (\d)/3", text)
        if match:
            new_stage_idx = int(match.group(1))
            if new_stage_idx != stage_idx:
                stage_idx = new_stage_idx
                stage_info = stages[stage_idx - 1]
                local_state = init_local_state(game, stage_info)
                show_stage(stage_info)

        show_state(game, local_state, stage_info)

        if "KMACTF{" in text:
            break


if __name__ == "__main__":
    main()
