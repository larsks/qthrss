import requests
import bs4

category_url = "https://swap.qth.com/index.php"


def get_categories():
    res = requests.get(category_url)
    res.raise_for_status()
    soup = bs4.BeautifulSoup(res.text, "html.parser")

    row = soup.find(
        "td", string=lambda text: "VIEW BY CATEGORY" in text if text else None
    ).parent

    cats = []
    while True:
        row = row.findNextSibling()
        if row is None:
            break
        if row.find("td", string=lambda text: "QUICK SEARCH" in text if text else None):
            break
        links = row.findAll("a")
        cats.extend((link.text, link["href"]) for link in links)

    return cats


if __name__ == "__main__":
    categories = get_categories()
    print(categories)
