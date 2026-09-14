FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir playwright==1.62.0 && playwright install --with-deps chromium
RUN useradd --create-home --uid 1000 renderer && mkdir -p /run/tracker-browser && chown renderer:renderer /run/tracker-browser && chmod 700 /run/tracker-browser
COPY deploy/browser/worker.py /worker.py
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONDONTWRITEBYTECODE=1
# Browser installation is performed as root; move its read-only executable cache.
RUN mv /root/.cache/ms-playwright /ms-playwright && chmod -R a+rX /ms-playwright
USER renderer
CMD ["python", "/worker.py"]
