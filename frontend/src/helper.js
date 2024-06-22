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

export function addMedia(url,data, content){

    return http('POST', url, data, content)

}

export function displayMedia(data, key = 1){
  const fileTypes = ['jpg', 'jpeg', 'png', 'pdf']

  var extension = data.split('.').pop().toLowerCase();

  if(extension == "jpg" || extension == "png" || extension == "jpg"){
    return (<div><img key = {key} src = {data}></img></div>)

  }else if(extension == "pdf"){
    
  }else{
    return (<div>{console.log("invalid type")}</div>) 
  }


 }


export function put(url, json) {
  return http('PUT', url, JSON.stringify(json))
}

export function httpDelete(url) {
  return http('DELETE', url, null)
}

export function by_id(data){
  
  return document.getElementById(data); 
}
