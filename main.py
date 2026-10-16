"""Entry point: python main.py [url ...]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from browser.app import run  # noqa: E402

if __name__ == '__main__':
    run(sys.argv[1:])
