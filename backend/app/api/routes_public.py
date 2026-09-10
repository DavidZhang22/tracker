from fastapi import APIRouter
from pydantic import BaseModel, HttpUrl

from app.services import ml, scraping
from app.services.scraping_links import get_chapter_links

router = APIRouter()


class ChapterLinksRequest(BaseModel):
    root_url: HttpUrl   # e.g. the main series/index page
    target_url: HttpUrl # e.g. a sample chapter URL you know belongs to the series
    title: str          # e.g. "youtube" or site name / label


class ChapterLinksResponse(BaseModel):
    links: list[str]


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class AnalyzeResponse(BaseModel):
    summary: str
    score: float


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    # 1. scrape
    text = await scraping.fetch_and_extract_text(req.url)

    # 2. run ML
    result = ml.predict_from_text(text)

    # 3. return response
    return AnalyzeResponse(summary=result["summary"], score=result["score"])


@router.post("/chapter-links", response_model=ChapterLinksResponse)
def chapter_links(req: ChapterLinksRequest):
    links = get_chapter_links(
        root_url=str(req.root_url),
        target_url=str(req.target_url),
        title=req.title,
    )
    return ChapterLinksResponse(links=links)