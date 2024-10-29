import os
import datetime
import bs4
import jinja2
import itertools
import logging
import re

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
CACHE_PATH = os.getenv("QTHRSS_CACHE_PATH", ".cache")
CACHE_LIFETIME = int(os.getenv("QTHRSS_CACHE_LIFETIME", 3600))
ENTRIES_PER_CATEGORY = int(os.getenv("QTHRSS_ENTRIES_PER_CATEGORY", 20))


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
    published: datetime.datetime
    updated: datetime.datetime | None
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
    search_url = "search-results.php"
    re_entry_metadata = re.compile(
        r"Listing #(?P<listingid>\d+) +- +Submitted on (?P<date_created>\d\d/\d\d/\d\d) "
        r"by Callsign (?P<callsign>[^ ,]+),? "
        r"(Modified on (?P<date_modified>\d\d/\d\d/\d\d),? )?"
        r"(Web Site: (?P<website>[^ ]+ ) )?"
        r"- IP: (?P<ip>.*)"
    )

    def __init__(self, entries_per_category=None):
        self.session = CachedSession(
            CACHE_PATH, backend="sqlite", serializer="json", expire_after=CACHE_LIFETIME
        )
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

    def get_listings_for_category(self, category_name: str):
        listings: list[Listing] = []
        category = self.categories[category_name]

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

        return self.listings_from_dl(dl)

    def listings_from_dl(self, dl):
        listings: list[Listing] = []
        for child in dl[0].findChildren(recursive=False):
            if child.name == "dt":
                title = child.text.strip()
            elif child.name == "dd":
                description = "\n".join(child.text.splitlines()[:2])
                published = None
                updated = None
                if mo := self.re_entry_metadata.search(description):
                    d_created = mo.group("date_created")
                    if d_created:
                        published = datetime.datetime.strptime(
                            d_created, "%m/%d/%y"
                        ).replace(tzinfo=datetime.timezone.utc)

                    d_modified = mo.group("date_modified")
                    if d_modified:
                        updated = datetime.datetime.strptime(
                            d_modified, "%m/%d/%y"
                        ).replace(tzinfo=datetime.timezone.utc)
                contact_url = child.find("a", string="Click to Contact")
                photo_url = child.find("a", string="Click Here to View Picture")
                listings.append(
                    Listing(
                        title=title,
                        published=published,
                        updated=updated,
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

    def add_feed_entries(self, feed, listings):
        for listing in listings:
            entry = feed.add_entry()
            entry.id(listing.view_url)
            entry.title(listing.title)
            entry.published(listing.published)
            # LKS: feedgen may override this with current date/time
            entry.updated(listing.updated)
            entry.link(href=listing.view_url, rel="alternate")
            entry.link(href=listing.contact_url, rel="related")
            entry.description(listing.description)

    def feed_for(self, category_name: str):
        category = self.categories[category_name]

        feed = FeedGenerator()
        feed.id(category.url)
        feed.title(f"QTH Classifieds - {category.title}")
        feed.link(href=category.url, rel="alternate")
        feed.description(category.title)

        self.add_feed_entries(feed, self.get_listings_for_category(category.title))

        return feed

    def simple_search_feed(self, query: str):
        # This is only for display purposes
        search_url = f"https://swap.qth.com/search-results.php?keywords={query}&fieldtosearch=titleordesc"

        feed = FeedGenerator()
        feed.id(search_url)
        feed.title(f"QTH Classifieds - Search - {query}")
        feed.link(href=search_url, rel="alternate")
        feed.description(f"Search for {query}")

        self.add_feed_entries(feed, self.simple_search(query))

        return feed

    def simple_search(self, query: str):
        # https://swap.qth.com/search-results.php?keywords=tm-v71&fieldtosearch=titleordesc
        soup = self.get_soup(
            self.search_url, params={"keywords": query, "fieldtosearch": "titleordesc"}
        )
        dl = soup.select("table dl")
        if not dl:
            return []

        return self.listings_from_dl(dl)


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
        caturls = {cat: f"{urlquote(cat)}.xml" for cat in qth.categories}
        return t.render(categories=caturls)

    @app.route("/feeds.txt")
    def feeds_txt():
        caturls = [
            f'http://{request.headers['host']}/feed/{urlquote(cat)}.xml'
            for cat in qth.categories
        ]
        return Response("\n".join(caturls), mimetype="text/plain")

    @app.route("/feed/<path:category_name>.xml")
    def feed_for(category_name: str):
        feed = qth.feed_for(category_name)
        return Response(feed.atom_str(pretty=True), mimetype="application/atom+xml")

    @app.route("/search/<keyword>")
    def search(keyword: str):
        feed = qth.simple_search_feed(keyword)
        return Response(feed.atom_str(pretty=True), mimetype="application/atom+xml")

    @app.route("/cache")
    def cacheinfo():
        ci = qth.session.cache
        return jsonify(
            {
                "count": ci.count(),
                "urls": ci.urls(),
            }
        )

    return app
