import string
from bs4 import BeautifulSoup, SoupStrainer
from urllib.parse import urlparse, unquote
import urllib3

from scipy import stats
import numpy as np

from collections import defaultdict
from bisect import bisect_right

import sys

http = urllib3.PoolManager()


class UrlObject():
    def __init__(self, url):

        if unquote(url) != url:
            url = unquote(url)

        self.raw_url = url
        self.url = url.rstrip("/")

        url_parsed = urlparse(self.url)

        self.hostname = url_parsed.hostname
        self.path = url_parsed.path
        self.query = url_parsed.query
        self.protocol = url_parsed.scheme

        self.split_path = [i for i in self.path.split("/") if i]
        self.path_params = self.path + "?" + self.query

    def __eq__(self, other):
        return (self.hostname == other.hostname and self.path == other.path)
    
    def __lt__(self, other):
        return self.path < other.path
    
    def __hash__(self):
        return hash(self.url)
    
    def __str__(self):
        return self.url
    
    def __repr__(self):
        return self.url

def get_content(url, title):

    chaps = []

    for page in range(1, 20): #nothing should need more than 20 pages

        
        url_page = url.url + f"?page={page}" if not url.query else url.url + f"&page={page}"
    
        response = http.request('GET', url.url if page == 1 else url_page)

        return_url = UrlObject(response.geturl())

        if response.status != 200 or not response.data:
            break

        if page!= 1 and url_page != return_url.url:
            break #no more pages
        
        links = BeautifulSoup(response.data, 'html.parser', parse_only=SoupStrainer('a'), )

        
        if title == "youtube":
            chaps.append([i for i in chaps if "watch" in i])
            continue
        
        for link in links:
            link_str = link.get("href")

            if  link_str and "" in link_str.lower():

                parsed_link = UrlObject(link_str)

                if parsed_link.hostname is None:
                    parsed_link.hostname = url.hostname
                    parsed_link.url =  url.hostname + link_str

                if not parsed_link.protocol:
                    parsed_link.protocol = url.protocol
                    parsed_link.url = url.protocol + "://" + link_str

                chaps.append(parsed_link)


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




def mask(arr, values,  cond):
    return [val for (i, val) in enumerate(arr) if cond(values[i])]

def remove_duplicates(seq):
    #keep last duplicate in list because links are grouped at end
    seq = seq[::-1]
    seen = set()
    result = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            result.append(item)
        
    return result[::-1]

def filter_links(target_link, links):
   
    link_sim = [(link, similarity(target_link, link)) for link in links]

    scores = [sim for _, sim in link_sim]

    #if all scores are the same (zscore fails in this case)

    if len(set(scores)) == 1:
            return links

    z = stats.zscore(scores)
    mean_z = np.mean(z)

    link_sim = mask(link_sim, z, lambda x: x > mean_z)

    link_sim = remove_duplicates(link_sim)

    return link_sim

def mean_filter(links):

    scores = [sim for _, sim in links]
    mean_s = np.mean(scores)
    links = mask(links, scores, lambda x: x > 0.8 * mean_s)

    return links

def find_numbering(link):

    path = link.path_params

    numbers = set()

    counter = 0

    for i in range(len(path)):
        if path[i].isdigit():
            counter += 1
        elif counter > 0:
            numbers.add(int(path[i - counter:i]))
            counter = 0

    return numbers

def numbering(links):
    numbers_arr = []
    
    for link in links:
        numbers = find_numbering(link)
        if numbers:
            numbers_arr.append(numbers)


    rev_numbers = numbers_arr[::-1]

    a = longest_incrementing_subsequence(numbers_arr)
    b = longest_incrementing_subsequence(rev_numbers)
    return a  > b 

#lis for list of list of numbers
def longest_incrementing_subsequence(nums):

    if not nums:
        return 0
    
    max_len = max([len(num_set) for num_set in nums])

    nums = [sorted(list(num_set)) for num_set in nums]

    dp_1 = [0]* max_len * len(nums)
    dp_2 = [0]* max_len * len(nums)

    for i in range(1, len(nums)):

        pos = bisect_right(nums[i], nums[i-1][0])-1

        for j in range(pos, len(nums[i])):
            
            j_pos = bisect_right(nums[i-1], nums[i][j])
            dp_1[j] = 1 + dp_2[j_pos]
        
        dp_2 = dp_1
        dp_1 = [0]* max_len * len(nums) 

    return max(dp_2)

def get_links(url, target, title):
   
    url = UrlObject(url)
    target = UrlObject(target)

    title = title.lower()
    links = get_content(url, title)
    res = filter_links(target, links)

    links = [link for link, _ in res]
    if not numbering(links):
        links = links[::-1]

    return links

def page_content(url): # make sure only scan non-content pages for change

    response = http.request('GET', url.url)

    if response.status == 404:
        return False
    
    len_content = len(BeautifulSoup(response.data, 'html.parser', parse_only=SoupStrainer('p') )) #TODO remove <p> tags for comments

    if len_content < 30: #TODO base this on size of previous content
        return False
        
    return True



f = get_links("https://lorenovels.com/surviving-in-a-romance-fantasy-novel/", "https://lorenovels.com/chapter-59-black-moon-unit-part-6/", "")
print(f)