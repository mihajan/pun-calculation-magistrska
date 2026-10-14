FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PUN_DATA_DIR=/app/data

WORKDIR /app

# Install the package (with the API extra) first, so this layer is cached
# as long as the code and dependencies do not change.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[api]"

# GME data: the XML files are extracted from the zips on first use.
COPY data/*.zip ./data/

RUN useradd --create-home app && chown -R app /app/data
USER app

EXPOSE 8000
CMD ["uvicorn", "pun_clearing.api:app", "--host", "0.0.0.0", "--port", "8000"]
