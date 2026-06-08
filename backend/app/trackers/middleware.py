import os


class SimpleCorsMiddleware:
    allowed_origins = {
        origin.strip()
        for origin in os.environ.get(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "OPTIONS":
            from django.http import HttpResponse

            response = HttpResponse()
        else:
            response = self.get_response(request)

        origin = request.headers.get("Origin")
        if origin in self.allowed_origins:
            response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        response["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response
