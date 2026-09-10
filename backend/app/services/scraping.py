import httpx
from bs4 import BeautifulSoup


async def fetch_and_extract_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    # naive: get visible text
    text = soup.get_text(separator=" ", strip=True)
    # you can refine this later
    return text
