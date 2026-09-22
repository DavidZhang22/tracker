export const mediaTypes = [
  ["comic", "Manga & comics"],
  ["novel", "Books & novels"],
  ["blog", "Blogs & articles"],
  ["youtube", "YouTube"],
  ["video", "Video & TV"],
  ["podcast", "Podcasts"],
  ["music", "Music"],
  ["events", "Events"],
  ["jobs", "Jobs"],
  ["software", "Software releases"],
  ["course", "Courses & tutorials"],
  ["research", "Research"],
  ["website", "Website"],
];
export const mediaLabel = (kind) =>
  mediaTypes.find(([value]) => value === kind)?.[1] || "Website";
