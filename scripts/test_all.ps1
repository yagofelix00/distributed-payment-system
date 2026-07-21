$ErrorActionPreference = "Stop"

python -m pytest payment-charges-api/tests -q
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$previousPythonPath = $env:PYTHONPATH

try {
    $env:PYTHONPATH = "fake-bank-service"
    python -m pytest fake-bank-service/tests -q

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPythonPath
    }
}
