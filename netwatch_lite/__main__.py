"""Start the app and the checker: python -m netwatch_lite [data.db] [--port 8050]"""
import sys

from .web import run

args = [a for a in sys.argv[1:] if not a.startswith("--")]
port = 8050
if "--port" in sys.argv:
    port = int(sys.argv[sys.argv.index("--port") + 1])
    args = [a for a in args if a != str(port)]
run(args[0] if args else "netwatch.db", port)
