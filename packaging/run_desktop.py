"""The entry point PyInstaller packages as AccessFromPhone.exe."""
import sys

from handoff.desktop import main

if __name__ == "__main__":
    sys.exit(main())
