import requests
import os

url = 'http://localhost:8001/api/v1/upload/cdr'
file_path = '../mock_cdr.csv'

with open(file_path, 'rb') as f:
    files = {'file': (os.path.basename(file_path), f, 'text/csv')}
    response = requests.post(url, files=files)

print('Status Code:', response.status_code)
print('Response JSON:', response.json())
