from hackbot_runtime import run_async

from .hackbot import main

if __name__ == "__main__":
    raise SystemExit(run_async(main))
