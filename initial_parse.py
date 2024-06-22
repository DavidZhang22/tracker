import string
from bs4 import BeautifulSoup, SoupStrainer
from urllib.parse import urlparse, unquote
import urllib3

from scipy import stats
import numpy as np

import sys

http = urllib3.PoolManager()

class UrlObject():
    def __init__(self, url):

        if unquote(url) != url:
            url = unquote(url)

        self.url = url

        url_parsed = urlparse(url)

        self.hostname = url_parsed.hostname
        self.path = url_parsed.path
        self.query = url_parsed.query
        self.protocol = url_parsed.scheme

        self.split_path = [i for i in self.path.split("/") if i]

    def __eq__(self, other):
        return (self.hostname == other.hostname and self.path == other.path)
    
    def __lt__(self, other):
        return self.path < other.path
    
    def __hash__(self):
        return hash(self.url)
    
def remove_duplicates(seq):
    seen = set()
    result = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

def getcontent(url, title:string):

    response = http.request('GET', url.url)
    links = BeautifulSoup(response.data, 'html.parser', parse_only=SoupStrainer('a'), )


    chaps = []
    
    if title == "youtube":
        chaps = [i for i in chaps if "watch" in i]
        return [*set(chaps)]
    

    for link in links:
        link_str = link.get("href")

        if  link_str and title in link_str.lower():

            parsed_link = UrlObject(link_str)

            if parsed_link.hostname is None:
                parsed_link.hostname = url.hostname
                parsed_link.url =  url.hostname + link_str

            if not parsed_link.protocol:
                parsed_link.protocol = url.protocol
                parsed_link.url = url.protocol + "://" + link_str

            chaps.append(parsed_link)


    chaps = remove_duplicates(chaps)

    return chaps

def similarity(a, b):

    score = 0
    sp_a = a.split_path
    sp_b = b.split_path

    arr_len = min(len(sp_a), len(sp_b))
    for path in range(arr_len):
        str_len = min(len(sp_a[path]), len(sp_b[path]))
        for char in range(min(len(sp_a[path]), len(sp_b[path]))):
            if  sp_a[path][char] == sp_b[path][char]:
                score += (arr_len - path) * (str_len - char) # early matches are more important
            else:
                break
        
    return score

def page_content(url):

    response = http.request('GET', url.url)

    if response.status == 404:
        return False
    
    len_content = len(BeautifulSoup(response.data, 'html.parser', parse_only=SoupStrainer('p') )) #TODO remove <p> tags for comments

    if len_content < 30: #TODO base this on size of previous content
        return False
        
    return True

def filter_links(target, links):
   
   target_link = UrlObject(target)
   link_sim = [(link, similarity(target_link, link)) for link in links]

   scores = [sim for _, sim in link_sim]
   z = stats.zscore(scores)

   mask = np.where(z > np.mean(z)) # more similar the better

   link_sim = [link_sim[i] for i in mask[0]]

   return link_sim



def main():

    examples = [("https://fourseasonsforest.wordpress.com/about-your-pride-and-my-prejudice/", "https://fourseasonsforest.wordpress.com/2021/12/14/about-your-pride-and-my-prejudice-01/"),
                ("https://lorenovels.com/surviving-in-a-romance-fantasy-novel/", "https://lorenovels.com/chapter-59-black-moon-unit-part-6/"),
                ("https://asuracomic.net/manga/magic-academys-genius-blinker/", "https://asuracomic.net/magic-academys-genius-blinker-chapter-22/"), 
                ("https://www.lightnovelworld.co/novel/advent-of-the-three-calamities/chapters", "https://www.lightnovelworld.co/novel/advent-of-the-three-calamities-1678/chapter-4"),
                ("https://genesistls.com/series/the-academys-weakest-became-a-demon-limited-hunter/", "https://genesistls.com/demon-limited-hunter-chapter-1/")]

    url = UrlObject(sys.argv[1])
    target = UrlObject(sys.argv[2])

    title =  sys.argv[3] if len(sys.argv) > 3 else ""

    links = getcontent(url, title)

    res = filter_links(target, links)

    for link, score in res:
        print(link.url)


if __name__ == "__main__":
    main()