import base64, os, select, sys, termios, time

ESC = b"\x1b"
GLYPH = "\U0010EEEE"
ROW_MARKS = ["\u0305", "\u030d", "\u030e", "\u0310"]

def cmd(payload: bytes) -> bytes:
    return ESC + b"_G" + payload + ESC + b"\\"

def cell(col: int, row: int, image_id: int) -> str:
    r, g, b = (image_id >> 16) & 255, (image_id >> 8) & 255, image_id & 255
    return (f"\x1b[38;2;{r};{g};{b}m\x1b[58;2;{r};{g};{b}m"
            f"{GLYPH}{ROW_MARKS[row % len(ROW_MARKS)]}{ROW_MARKS[col % len(ROW_MARKS)]}\x1b[39;59m")

def grid(columns: int, rows: int, image_id: int) -> bytes:
    return ("\r\n".join("".join(cell(c, r, image_id) for c in range(columns)) for r in range(rows))).encode()

def build_files(png_path: str) -> bytes:
    """Per-id oracle: direct upload (1), good t=f (2), nothing (3), bad-path t=f (4)."""
    data = open(png_path, "rb").read()
    out = bytearray()
    out += cmd(b"a=t,f=100,i=1,q=2,m=0;" + base64.b64encode(data))
    out += cmd(b"a=p,i=1,p=1,U=1,c=3,r=1,q=2;")
    out += cmd(b"a=t,f=100,t=f,i=2,q=2;" + base64.b64encode(png_path.encode()))
    out += cmd(b"a=p,i=2,p=2,U=1,c=3,r=1,q=2;")
    out += cmd(b"a=p,i=3,p=3,U=1,c=3,r=1,q=2;")
    out += cmd(b"a=t,f=100,t=f,i=4,q=2;" + base64.b64encode(b"/tmp/gprobe/definitely-missing.png"))
    out += cmd(b"a=p,i=4,p=4,U=1,c=3,r=1,q=2;")
    for image_id in (1, 2, 3, 4):
        out += grid(3, 1, image_id) + b"\r\n"
    return bytes(out)


def tmuxwrap(seq: bytes) -> bytes:
    return ESC + b"Ptmux;" + seq.replace(ESC, ESC + ESC) + ESC + b"\\"


def build_tmux3(png_path: str) -> bytes:
    """id 3: wrapped placement + UNWRAPPED cells (grid path).
       id 8: wrapped placement + WRAPPED cells (passthrough path)."""
    out = bytearray()
    out += tmuxwrap(cmd(b"a=p,i=3,p=3,U=1,c=3,r=1,q=2;"))
    out += grid(3, 1, 3) + b"\r\n\r\n"
    out += tmuxwrap(cmd(b"a=p,i=8,p=8,U=1,c=3,r=1,q=2;"))
    out += tmuxwrap(grid(3, 1, 8)) + b"\r\n"
    return bytes(out)


def build_tmux2(png_path: str) -> bytes:
    """Three oracles through an attached tmux client: grid cells vs passthrough bytes."""
    out = bytearray()
    out += grid(3, 1, 3) + b"\r\n\r\n"                       # unwrapped cells -> expect dropped
    out += tmuxwrap(cmd(b"a=p,i=5,p=5,U=1,c=1,r=1,q=2;"))     # wrapped placement, id never uploaded
    out += tmuxwrap(grid(3, 1, 7)) + b"\r\n"                  # wrapped cells, id never uploaded
    return bytes(out)


def build(png_path: str, mode: str) -> bytes:
    data = open(png_path, "rb").read()
    b64 = base64.b64encode(data)
    out = bytearray()
    if mode == "tmux":
        out += grid(3, 1, 3) + b"\r\n"
        return bytes(out)
    # 1) direct chunked upload (control) for id 1
    chunk = b64[:4096]
    out += cmd(b"a=t,f=100,i=1,q=2,m=0;" + chunk)
    out += cmd(b"a=p,i=1,p=1,U=1,c=2,r=1,q=2;")
    out += cmd(b"a=q,i=1,q=2;")
    # 2) regular-file transfer for id 2
    out += cmd(b"a=t,f=100,t=f,i=2,q=2;" + base64.b64encode(png_path.encode()))
    out += cmd(b"a=p,i=2,p=2,U=1,c=2,r=1,q=2;")
    out += cmd(b"a=q,i=2,q=2;")
    # 3) placeholder cells for an image that was never uploaded
    out += grid(3, 1, 3) + b"\r\n"
    return bytes(out)

def main() -> int:
    mode, png_path, result_path = sys.argv[1], sys.argv[2], sys.argv[3]
    payload = build_files(png_path) if mode == "files" else build_tmux3(png_path) if mode == "tmux3" else build_tmux2(png_path) if mode == "tmux2" else build(png_path, mode)
    if mode != "tmux":
        # Raw mode is required: kitty responses carry no newline, so a cooked tty would
        # hold them in the line discipline and the probe would read nothing.
        attrs = termios.tcgetattr(0)
        raw = termios.tcgetattr(0)
        raw[3] = raw[3] & ~(termios.ICANON | termios.ECHO)
        raw[6][termios.VMIN] = 0
        raw[6][termios.VTIME] = 0
        termios.tcsetattr(0, termios.TCSANOW, raw)
    os.write(1, payload)
    got = bytearray()
    if mode not in ("tmux", "tmux2", "tmux3"):
        deadline = time.time() + 3.0
        while time.time() < deadline:
            ready, _, _ = select.select([0], [], [], 0.2)
            if ready:
                chunk = os.read(0, 4096)
                if not chunk:
                    break
                got += chunk
    if mode != "tmux":
        termios.tcsetattr(0, termios.TCSANOW, attrs)
    with open(result_path, "w") as fh:
        fh.write(f"mode={mode}\nbytes_written={len(payload)}\nresponses={got!r}\n")
    time.sleep(0.2)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
