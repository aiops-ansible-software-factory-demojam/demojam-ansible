"""Independent application inputs; never imports the candidate's probe or fixtures."""
import json
import time
from urllib.request import Request, urlopen
from fixtures import invoice_pdf

for regression in (False, True):
    started = time.monotonic()
    request = Request('http://127.0.0.1/api/process', data=invoice_pdf(regression),
                      headers={'Content-Type': 'application/pdf'})
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
    if (result.get('status') != 'completed'
            or 'Invoice INV-1042 - Example Customer - USD 125.00' not in result.get('text', '')
            or time.monotonic() - started >= 2):
        raise SystemExit('Independent normal/regression document acceptance failed')
print(json.dumps({'normal': 'passed', 'regression': 'passed', 'deadline_seconds': 2}))
