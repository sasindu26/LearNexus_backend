"""
Scrape NSBM Faculty of Computing degree programmes and import into Neo4j.
Creates Course nodes and Module nodes with CONTAINS relationships.
Run: python scripts/scrape_nsbm.py
"""

import sys, os, time, re
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests
from bs4 import BeautifulSoup
from app.core.database import run_query

FACULTY_URL = "https://www.nsbm.ac.lk/foc-degree/"
HEADERS = {"User-Agent": "Mozilla/5.0 (LearNexus scraper; research use)"}


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_degree_links() -> list[dict]:
    soup = fetch(FACULTY_URL)
    degrees = []
    for a in soup.select("a[href*='/course/']"):
        href = a["href"].strip()
        # get text from the nearest heading or the link itself
        title = a.get_text(strip=True)
        if not title:
            heading = a.find(["h2", "h3", "h4", "p"])
            title = heading.get_text(strip=True) if heading else href
        if href and title and href not in [d["url"] for d in degrees]:
            degrees.append({"url": href, "name": title})
    return degrees


def clean_module_name(raw: str) -> str:
    name = re.sub(r"^[>\-\*\•\s]+", "", raw).strip()
    name = re.sub(r"\s+", " ", name)
    return name


def scrape_degree(url: str) -> dict:
    soup = fetch(url)

    # Get degree title from page <h1>
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else url.split("/")[-2].replace("-", " ").title()

    # Get description from aims or intro paragraph
    desc = ""
    aims_heading = soup.find(lambda t: t.name in ["h2","h3"] and "aim" in t.get_text(strip=True).lower())
    if aims_heading:
        p = aims_heading.find_next("li")
        if p:
            desc = p.get_text(strip=True)[:300]

    # Find "PROGRAMME CONTENTS" section then extract years
    modules_by_year = {}
    current_year = None

    # Look for year headings (Year 1, Year 2, Year 3)
    for tag in soup.find_all(["h2", "h3", "h4", "th", "td", "div", "p"]):
        text = tag.get_text(strip=True)
        year_match = re.match(r"^Year\s*(\d)", text, re.I)
        if year_match:
            current_year = int(year_match.group(1))
            modules_by_year[current_year] = []
            # Grab sibling/child <ul> or <li> items
            ul = tag.find_next("ul")
            if ul:
                for li in ul.find_all("li"):
                    name = clean_module_name(li.get_text())
                    if name and len(name) > 3:
                        modules_by_year[current_year].append(name)

    return {"title": title, "description": desc, "url": url, "modules_by_year": modules_by_year}


def import_to_neo4j(degree: dict) -> tuple[int, int]:
    course_name = degree["title"]
    desc = degree["description"]
    url = degree["url"]

    # Create/update Course node
    run_query(
        """
        MERGE (c:Course {name: $name})
        SET c.description = $description,
            c.url = $url,
            c.university = 'NSBM Green University',
            c.faculty = 'Faculty of Computing'
        """,
        {"name": course_name, "description": desc, "url": url},
    )

    modules_created = 0
    for year, modules in degree["modules_by_year"].items():
        for module_name in modules:
            if not module_name:
                continue
            run_query(
                """
                MERGE (m:Module {name: $name})
                SET m.year = $year,
                    m.source = 'NSBM'
                """,
                {"name": module_name, "year": year},
            )
            run_query(
                """
                MATCH (c:Course {name: $course}), (m:Module {name: $module})
                MERGE (c)-[:CONTAINS]->(m)
                """,
                {"course": course_name, "module": module_name},
            )
            modules_created += 1

    return 1, modules_created


def main():
    print("==> Fetching degree list from NSBM FOC...")
    links = get_degree_links()
    print(f"  Found {len(links)} degree links")

    total_courses = 0
    total_modules = 0

    for i, link in enumerate(links, 1):
        url = link["url"]
        print(f"\n[{i}/{len(links)}] Scraping: {url}")
        try:
            degree = scrape_degree(url)
            mod_count = sum(len(m) for m in degree["modules_by_year"].values())
            print(f"  Title: {degree['title']}")
            print(f"  Years found: {sorted(degree['modules_by_year'].keys())}")
            print(f"  Modules: {mod_count}")
            if mod_count == 0:
                print("  WARNING: No modules found, skipping import")
                continue
            courses, modules = import_to_neo4j(degree)
            total_courses += courses
            total_modules += modules
            time.sleep(1)  # polite delay
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

    print(f"\n==> Done. {total_courses} courses, {total_modules} module links imported into Neo4j.")

    # Verify
    result = run_query("MATCH (c:Course) RETURN count(c) AS courses")
    modules = run_query("MATCH (m:Module) RETURN count(m) AS modules")
    print(f"==> Neo4j now has {result[0]['courses']} courses, {modules[0]['modules']} modules")


if __name__ == "__main__":
    main()
