FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /service
COPY miniapp/requirements.txt /service/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m app && mkdir /data && chown app:app /data
COPY miniapp /service/miniapp
USER app
EXPOSE 8080
CMD ["python", "-m", "miniapp.server"]
