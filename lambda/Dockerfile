FROM public.ecr.aws/lambda/python:3.12

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Chromium system dependencies via dnf (Amazon Linux 2023).
# playwright install --with-deps does not support AL2023 — we handle deps manually.
RUN dnf install -y \
    alsa-lib \
    at-spi2-atk \
    atk \
    cups-libs \
    gtk3 \
    libX11 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXfixes \
    libXi \
    libXrandr \
    libXtst \
    libxcb \
    libxkbcommon \
    libdrm \
    mesa-libgbm \
    nss \
    pango \
    && dnf clean all

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY kita_doodle_lambda.py ${LAMBDA_TASK_ROOT}/

CMD ["kita_doodle_lambda.handler"]
