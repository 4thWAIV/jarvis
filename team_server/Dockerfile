# The JARVIS team server. Built from this folder alone (the deploy package);
# none of the desktop application is in the image.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY teamserver /app/teamserver
# Written by package.sh from a clean Git checkout: the commit this image is.
COPY build_info.json /app/build_info.json
RUN useradd --system --uid 10001 teamserver
USER teamserver
EXPOSE 8080
CMD ["python", "-m", "teamserver"]
