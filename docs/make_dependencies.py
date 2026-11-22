"""把 dependencies.json（相依圖的資料）寫成好讀的 dependencies.md。用法：python docs/make_dependencies.py"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIND = {"import": "改壞會報錯", "name": "改壞不會報錯"}


def render(data: dict) -> str:
    name = {n["id"]: n["name"] for n in data["nodes"]}
    out = ["# 相依圖：改了哪裡，就回去檢查哪裡", "",
           "這份文件是從 `dependencies.json` 做出來的（`python docs/make_dependencies.py`），不要直接改。",
           "改完任何東西之前先查這裡：找到你改的那一項，把列出來的地方都看過一遍，需要的話一起修。", "",
           "相依有兩種。**改壞會報錯**的是程式直接載入的；**改壞不會報錯**的只靠名字對上（設定名稱、文字的 key、API 路徑、檔名），",
           "最容易漏掉，`tests/test_basics.py` 的 `test_dependencies_hold` 會自動檢查其中能檢查的。", "",
           "## 我改了什麼", ""]
    for rule in data["rules"]:
        out += [f"### {rule['title']}", ""]
        for k, step in enumerate(rule["steps"], 1):
            out.append(f"{k}. {'、'.join(f'`{name[i]}`' for i in step['nodes'])}：{step['check']}")
        out += ["", f"自動把關：{rule['guard']}", ""]
    out += ["## 每個部分牽動誰", ""]
    for row in data["rows"]:
        out += [f"### {row['title']}（{row['what']}）", "", "| 部分 | 檔案 | 改了它，要回去檢查 |", "| --- | --- | --- |"]
        for node in (n for n in data["nodes"] if n["row"] == row["id"]):
            users = [e for e in data["edges"] if e["to"] == node["id"] and e["from"] not in ("tests", "selftest")]
            cell = "<br>".join(f"`{name[e['from']]}`：{e['what']}（{KIND[e['kind']]}）" for e in users) or "沒有別的地方用到它"
            files = "<br>".join(f"`{f}`" for f in node["files"])
            out.append(f"| {node['role']} | {files} | {cell} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    text = render(json.loads((HERE / "dependencies.json").read_text(encoding="utf-8")))
    (HERE / "dependencies.md").write_text(text, encoding="utf-8", newline="\n")
    print("wrote", HERE / "dependencies.md")
