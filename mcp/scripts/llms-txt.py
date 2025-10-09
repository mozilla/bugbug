"""Generate llms.txt file from Firefox Source Documentation.

This script crawls https://firefox-source-docs.mozilla.org/ and generates
a structured llms.txt file with links to the source documentation files.
"""

import logging
import os
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scripts.llms-txt")

BASE_URL = "https://firefox-source-docs.mozilla.org/"
SOURCE_URL = f"{BASE_URL}_sources/"
CACHE: Dict[str, Optional[str]] = {}


def fetch_content(url: str) -> Optional[str]:
    """Fetch content from URL with caching."""
    if url in CACHE:
        return CACHE[url]

    response = requests.get(url, timeout=30)
    if response.status_code == 200:
        content = response.text
    elif response.status_code == 404:
        content = None
    else:
        response.raise_for_status()

    CACHE[url] = content
    return content


def convert_html_to_source_url(html_url: str) -> Optional[str]:
    """Convert HTML URL to source .rst.txt or .md.txt URL.

    Args:
        html_url: URL ending in .html

    Returns:
        URL to .rst.txt or .md.txt if found, None otherwise
    """
    # Convert relative URL to absolute
    if not html_url.startswith("http"):
        html_url = urljoin(BASE_URL, html_url)

    # Remove .html extension
    if html_url.endswith(".html"):
        base_path = html_url[:-5]  # Remove .html
        # Extract path after base URL
        path = base_path.replace(BASE_URL, "")

        # Try .rst.txt first
        rst_url = f"{SOURCE_URL}{path}.rst.txt"
        if fetch_content(rst_url):
            return rst_url

        # Try .md.txt
        md_url = f"{SOURCE_URL}{path}.md.txt"
        if fetch_content(md_url):
            return md_url

    return None


def has_toctree(rst_content: str) -> bool:
    """Check if RST content contains toctree directive."""
    return ".. toctree::" in rst_content


def extract_links_from_html(html_url: str) -> List[Tuple[str, str]]:
    """Extract links from HTML page.

    Args:
        html_url: URL of the HTML page

    Returns:
        List of (link_url, link_text) tuples
    """
    content = fetch_content(html_url)
    if not content:
        return []

    soup = BeautifulSoup(content, "html.parser")

    # Find all links in toctree sections
    links = []

    # Look for div.toctree-wrapper or similar structures
    toctree_divs = soup.find_all("div", class_="toctree-wrapper")

    if toctree_divs:
        for div in toctree_divs:
            for link in div.find_all("a", href=True):
                href = link.get("href")
                text = link.get_text(strip=True)
                # Skip anchor links
                if href and not href.startswith("#"):
                    full_url = urljoin(html_url, href)
                    links.append((full_url, text))
    else:
        # Fallback: get all links from the main content area
        main_content = soup.find("div", {"role": "main"}) or soup.find("main") or soup
        for link in main_content.find_all("a", href=True):
            href = link.get("href")
            text = link.get_text(strip=True)
            if href and not href.startswith("#"):
                full_url = urljoin(html_url, href)
                links.append((full_url, text))

    return links


def process_link(
    html_url: str,
    visited: Set[str],
    depth: int = 0,
    title: Optional[str] = None,
) -> List[Dict]:
    """Process a link recursively, handling toctree directives.

    Args:
        html_url: HTML URL to process
        visited: Set of already visited URLs (for cycle detection)
        depth: Current recursion depth
        fallback_title: Optional title to use if extraction from content fails

    Returns:
        List of dict entries with 'url', 'title', 'children' keys
    """
    # Limit recursion depth
    if depth > 5:
        logger.warning("Max recursion depth reached for %s", html_url)
        return []

    # Skip if already visited
    normalized_url = html_url.split("#")[0]  # Remove anchor
    if normalized_url in visited:
        return []

    visited.add(normalized_url)

    # Convert to source URL
    source_url = convert_html_to_source_url(normalized_url)
    if not source_url:
        return []

    # Get the content
    content = fetch_content(source_url)
    if not content:
        return []

    result = {"url": source_url, "title": title, "children": []}

    # Check for toctree
    if has_toctree(content):
        # Get links from the HTML version
        html_links = extract_links_from_html(normalized_url)

        for link_url, link_text in html_links:
            # Skip external links and anchor links
            if not link_url.startswith(BASE_URL):
                continue
            if "#" in link_url:
                continue

            # Recursively process, passing the link text as fallback title
            children = process_link(link_url, visited, depth + 1, title=link_text)
            result["children"].extend(children)

    return [result]


def parse_main_page() -> List[Dict]:
    """Parse the main documentation page and extract sections with links.

    Returns:
        List of sections, each with 'section_name' and 'links' keys
    """
    content = fetch_content(BASE_URL)
    if not content:
        raise RuntimeError(f"Failed to fetch main page from {BASE_URL}")

    soup = BeautifulSoup(content, "html.parser")
    sections = []

    # Find the main content area
    main_content = soup.find("div", {"role": "main"}) or soup.find("main") or soup.body
    if not main_content:
        return []

    # Firefox docs use toctree-wrapper divs
    toctree_wrappers = main_content.find_all("div", class_="toctree-wrapper")

    if toctree_wrappers:
        # Parse toctree-wrapper structure
        for wrapper in toctree_wrappers:
            # The section name is often in the first link or in a heading before this wrapper
            # Let's look for a paragraph or heading right before the ul
            section_name = None

            # Try to find a p.caption tag with section name
            caption = wrapper.find("p", class_="caption")
            if caption:
                section_name = caption.get_text(strip=True)
            else:
                # Extract from first part of content
                ul = wrapper.find("ul")
                if ul:
                    # Get section name from the text content or first link
                    text_content = wrapper.get_text(strip=True)
                    # Take first line/phrase as section name
                    first_line = text_content.split("\n")[0].strip()
                    if first_line:
                        section_name = first_line.split("(")[
                            0
                        ].strip()  # Remove (if any)

            if not section_name:
                continue

            # Skip non-content sections
            if section_name in ["Contents", "Indices and tables"]:
                continue

            current_section = {"section_name": section_name, "links": []}

            # Find direct child ul elements
            ul = wrapper.find("ul", recursive=False)
            if ul:
                # Get only top-level li elements (direct children)
                for li in ul.find_all("li", recursive=False):
                    link = li.find("a", href=True)
                    if link:
                        href = link.get("href")
                        # Skip anchor links (both starting with # and containing #)
                        if href and "#" not in href:
                            full_url = urljoin(BASE_URL, href)
                            text = link.get_text(strip=True)
                            current_section["links"].append(
                                {"url": full_url, "text": text}
                            )

            if current_section["links"]:
                sections.append(current_section)
    else:
        # Fallback: older structure without <section> tags
        current_section = None

        for element in main_content.find_all(["h2", "h3", "ul"]):
            if element.name in ["h2", "h3"]:
                # New section
                section_name = element.get_text(strip=True)
                # Skip TOC and other non-section headers
                if section_name and section_name not in [
                    "Contents",
                    "Indices and tables",
                ]:
                    current_section = {"section_name": section_name, "links": []}
                    sections.append(current_section)

            elif element.name == "ul" and current_section is not None:
                # Extract links from this list
                for li in element.find_all("li", recursive=False):
                    link = li.find("a", href=True)
                    if link:
                        href = link.get("href")
                        # Skip anchor links (both starting with # and containing #)
                        if href and "#" not in href:
                            full_url = urljoin(BASE_URL, href)
                            text = link.get_text(strip=True)
                            current_section["links"].append(
                                {"url": full_url, "text": text}
                            )

    return sections


def format_entry(entry: Dict, level: int = 0) -> List[str]:
    """Recursively format an entry and its children as markdown lines.

    Args:
        entry: Dict with 'url', 'title', and 'children' keys
        level: Current level (0 = top level)

    Returns:
        List of formatted markdown lines
    """
    lines = []
    indent = "  " * level
    address = entry["url"][len(SOURCE_URL) : -len(".txt")]
    lines.append(f"{indent}- [{entry['title']}]({address})")

    # Recursively add children
    for child in entry.get("children", []):
        lines.extend(format_entry(child, level + 1))

    return lines


def generate_llms_txt():
    """Generate the llms.txt file."""
    logger.info("Starting llms.txt generation...")
    logger.info("Fetching main page...")
    sections = parse_main_page()
    logger.info("Found %d sections", len(sections))

    if not sections:
        logger.error("No sections found")
        return

    visited: Set[str] = set()
    output_lines = []

    # Header
    output_lines.append("# Firefox Source Tree Documentation")
    output_lines.append("")
    output_lines.append("> Comprehensive documentation for Firefox development")
    output_lines.append("")
    output_lines.append(
        "To read any document below, use the `docs://` URI scheme to retrieve it as a resource from this MCP server."
    )
    output_lines.append("")

    # Process each section
    for section in sections:
        section_name = section["section_name"]
        logger.info("Processing section: %s", section_name)

        output_lines.append(f"## {section_name}")
        output_lines.append("")

        for link_info in section["links"]:
            html_url = link_info["url"]
            link_text = link_info["text"]

            logger.debug("Processing: %s (%s)", link_text, html_url)

            # Process the link and its children, passing link text as fallback title
            results = process_link(html_url, visited, title=link_text)

            for result in results:
                # Recursively format the entry and all its descendants
                output_lines.extend(format_entry(result))
                output_lines.append("")

    # Write to file
    directory_path = "static"
    file_name = "llms.txt"
    output_file = os.path.join(directory_path, file_name)
    os.makedirs(directory_path, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    logger.info("Generated %s", output_file)


if __name__ == "__main__":
    generate_llms_txt()
