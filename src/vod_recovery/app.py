import sys

from .common import load_config, print_text
from .flows import run_interactive_app


def main():
    load_config()
    try:
        run_interactive_app()
    except KeyboardInterrupt:
        print_text("Exiting.", before=2)
        sys.exit(0)
