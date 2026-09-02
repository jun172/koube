from pathlib import Path
from dotenv import load_dotenv
import os

#ファイルの場所を基準に、一つ上の階層(ルート)にある.envを指定
base_dir = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=base_dir/'.env')
