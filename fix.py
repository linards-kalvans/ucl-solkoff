import re
with open("frontend/index.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Fix Tabs: Make ONLY Round of 16 active
html = html.replace("class=\"tab-btn active\"", "class=\"tab-btn\"")
html = html.replace("class=\"tab-pane active\"", "class=\"tab-pane\"")
html = html.replace(
    "class=\"tab-btn\" data-tab=\"round-of-16\" role=\"tab\" aria-selected=\"false\"",
    "class=\"tab-btn active\" data-tab=\"round-of-16\" role=\"tab\" aria-selected=\"true\""
)
html = html.replace(
    "class=\"tab-btn\" data-tab=\"round-of-16\" role=\"tab\" aria-selected=\"true\"",
    "class=\"tab-btn active\" data-tab=\"round-of-16\" role=\"tab\" aria-selected=\"true\""
)
html = html.replace(
    "id=\"round-of-16-tab\" class=\"tab-pane\"",
    "id=\"round-of-16-tab\" class=\"tab-pane active\""
)

# 2. Fix Text: Replace all "Historical analysis..." with the ELO link
pattern = re.compile(r"Historical analysis based on common opponents \(last <span id=\"historicalYears\d*\">\d+</span>\s*years\)")
replacement = "ELO coefficient based strength predictions <a href=\"https://en.wikipedia.org/wiki/Elo_rating_system\" target=\"_blank\">what is ELO?</a>"
html = pattern.sub(replacement, html)

with open("frontend/index.html", "w", encoding="utf-8") as f:
    f.write(html)
