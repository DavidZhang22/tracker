export const serverOrigin = 'http://localhost:8080'

export function http(method, url, body, content = 'application/json') {
  if (!url.includes('http')) {
    url = serverOrigin + url
  }

  return window.fetch(url, {
    method,
    request_uid: "1",
    credentials: 'include',
    headers: {
      'Content-Type': content
    },
    body
  }).then(res => res.json())
}

export function get(url) {
  return http('GET', url, null)
}

export function post(url, json) {
  return http('POST', url, JSON.stringify(json))

}


export function put(url, json) {
  return http('PUT', url, JSON.stringify(json))
}
