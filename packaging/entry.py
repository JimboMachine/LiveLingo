"""打包成 LiveLingo.exe 時的進入點。"""

import os

from livelingo.__main__ import main

if __name__ == "__main__":
    os._exit(main())
