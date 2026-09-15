I built Trackify, a web app that brings blogs, serial chapters, videos, game updates and job listings into one personal library. Users add a URL, and Trackify finds relevant links, associates dates and other metadata, and tracks what is new or unread through a streamlined, mobile-friendly interface.

The main engineering challenge was extracting useful links from inconsistent layouts without expensive scraping or cloud inference. I developed a lightweight ML pipeline combining a small neural network for identifying each link's surrounding record with compact tree classifiers for separating content from navigation and promotional links. I built datasets from public HTML examples and authored layout variations, engineered bounded URL and DOM features, and evaluated models across separate source and layout groups. The models run locally on CPUs using numeric weights, without a GPU or a production ML framework.

I also optimized the surrounding system: it prefers APIs and feeds, learns lightweight extraction rules for repeat refreshes, and analyzes pages in parallel. On a saved four-page benchmark, this reduced batch analysis time by 27% while preserving identical results. I built and deployed the full React, FastAPI and SQLite application with HTTPS, private user accounts, request limits, data export and account deletion.

Try it: https://mediatrackify.duckdns.org
Source: https://github.com/DavidZhang22/tracker
