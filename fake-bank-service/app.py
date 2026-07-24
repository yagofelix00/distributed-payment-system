from flask import Flask, g
from routes.pix import pix_bp
from audit.request_context import init_request_id, REQUEST_ID_HEADER
from routes.dlq import dlq_bp
from routes.health import health_bp


app = Flask(__name__)
app.register_blueprint(pix_bp)
app.register_blueprint(dlq_bp)
app.register_blueprint(health_bp)

@app.before_request
def before_request():
    init_request_id()

@app.after_request
def after_request(response):
    response.headers[REQUEST_ID_HEADER] = g.request_id
    return response

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000, debug=True)


