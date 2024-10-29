import os
import bs4
import jinja2
import itertools
import logging

from typing import override

from requests_cache import CachedSession
from dataclasses import dataclass
from urllib.parse import urljoin
from urllib.parse import quote as urlquote
from feedgen.feed import FeedGenerator
from flask import Flask
from flask import Response
from flask import request
from flask import jsonify

LOG = logging.getLogger(__name__)
CACHE_PATH = os.getenv('QTHRSS_CACHE_PATH', '.cache')
CACHE_LIFETIME = int(os.getenv('QTHRSS_CACHE_LIFETIME', 3600))
ENTRIES_PER_CATEGORY = int(os.getenv('QTHRSS_ENTRIES_PER_CATEGORY', 20))

@dataclass
class Category:
    url: str
    title: str

    @override
    def __str__(self):
        return self.title


@dataclass
class Listing:
    title: str
    description: str
    contact_url: str
    view_url: str
    photo_url: str | None = None


class QTHRSS:
    listings: dict[str, list[Listing]] = {}
    categories: dict[str, Category] = {}
    entries_per_category = 20
    base_url = "https://swap.qth.com"
    category_listing_url = "index.php"

    def __init__(self, entries_per_category=None):
        self.session = CachedSession(CACHE_PATH, backend="sqlite", serializer="json", expire_after=CACHE_LIFETIME)
        self.listings = {}
        self.categories = {}

        if entries_per_category:
            self.entries_per_category = entries_per_category

    def get_soup(
        self, url: str, params: dict[str, str] | None = None
    ) -> bs4.BeautifulSoup:
        url = urljoin(self.base_url, url)
        res = self.session.get(url, params=params)
        res.raise_for_status()
        return bs4.BeautifulSoup(res.text, "lxml")

    def get_categories(self) -> list[Category]:
        soup = self.get_soup(self.category_listing_url)

        row = soup.find(
            "td", string=lambda text: "VIEW BY CATEGORY" in text if text else None
        ).parent

        while True:
            row = row.findNextSibling()
            if row is None:
                break
            if row.find(
                "td", string=lambda text: "QUICK SEARCH" in text if text else None
            ):
                break
            links = row.findAll("a")
            self.categories.update(
                {
                    link.text.strip(): Category(
                        url=link["href"], title=link.text.strip()
                    )
                    for link in links
                }
            )

    def get_listings_for_category(self, category: Category):
        listings: list[Listing] = []

        page = itertools.count(start=1)
        while len(listings) < self.entries_per_category:
            batch = self._get_listings_for_category(category, page=next(page))
            if not batch:
                break
            listings.extend(batch)

        return listings

    def _get_listings_for_category(self, category: Category, page: int = 1):
        soup = self.get_soup(category.url, params={"page": page})
        dl = soup.select(".qth-content-wrap dl")
        if not dl:
            return []

        listings: list[Listing] = []
        for child in dl[0].findChildren(recursive=False):
            if child.name == "dt":
                title = child.text.strip()
            elif child.name == "dd":
                description = "\n".join(child.text.splitlines()[:2])
                contact_url = child.find("a", string="Click to Contact")
                photo_url = child.find("a", string="Click Here to View Picture")
                listings.append(
                    Listing(
                        title=title,
                        description=description,
                        contact_url=urljoin(self.base_url, contact_url["href"]),
                        view_url=urljoin(
                            self.base_url,
                            contact_url["href"].replace("contact", "view_ad"),
                        ),
                        photo_url=urljoin(self.base_url, photo_url["href"])
                        if photo_url
                        else None,
                    )
                )

        return listings

    def feed_for(self, category_name: str):
        category = self.categories[category_name]

        feed = FeedGenerator()
        feed.id(category.url)
        feed.title(f"QTH Classifieds - {category.title}")
        feed.link(href=category.url, rel="self")
        feed.description(category.title)

        for listing in self.get_listings_for_category(category):
            entry = feed.add_entry()
            entry.id(listing.view_url)
            entry.title(listing.title)
            entry.link(href=listing.view_url, rel="alternate")
            entry.link(href=listing.contact_url, rel="related")
            entry.description(listing.description)

        return feed


def create_app():
    app = Flask(__name__)
    qth = QTHRSS(entries_per_category=ENTRIES_PER_CATEGORY)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader("templates"))

    @app.before_request
    def update_categories():
        qth.get_categories()

    @app.route("/")
    def feeds():
        t = env.get_template("feeds.html")
        caturls = {cat: f'{urlquote(cat)}.xml' for cat in qth.categories}
        return t.render(categories=caturls)

    @app.route("/feeds.txt")
    def feeds_txt():
        caturls = [f'http://{request.headers['host']}/feed/{urlquote(cat)}.xml' for cat in qth.categories]
        return Response('\n'.join(caturls), mimetype='text/plain')

    @app.route("/feed/<path:category_name>.xml")
    def feed_for(category_name: str):
        feed = qth.feed_for(category_name)
        return Response(feed.atom_str(), mimetype="application/atom+xml")

    @app.route('/cache')
    def cacheinfo():
        ci = qth.session.cache
        return jsonify({
            'count': ci.count(),
            'urls': ci.urls(),
        })

    return app
