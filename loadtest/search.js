import http

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000'

export default function () {
  const params: { name: string } = { name: 'search' }

  http.get(`${BASE_URL}/search?q=Zermatt&page_size=20`, null, params)

  http.get(`${BASE_URL}/search?place_type=poi&page_size=20`, null, params)

  http.get(`${BASE_URL}/nearby?lat=46.0207&lon=7.7491&radius_m=5000&page_size=20`, null, params)

  http.get(`${BASE_URL}/classifications/categories`, null, params)
}

export const options = {
  stages: [
    { duration: '30s', target: 50 },
    { duration: '1m', target: 50 },
    { duration: '30s', target: 100 },
    { duration: '1m', target: 100 },
    { duration: '30s', target: 50 },
  ],
}
