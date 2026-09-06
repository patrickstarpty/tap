ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS dependencies
WORKDIR /build
COPY wheelhouse /build/wheelhouse
COPY requirements.txt /build/requirements.txt
RUN python -I -m pip install --no-index --find-links=/build/wheelhouse --require-hashes --no-deps --only-binary=:all: --no-compile --target=/opt/parser/site -r requirements.txt
FROM ${PYTHON_IMAGE} AS production
COPY --from=dependencies /opt/parser/site /opt/parser/site
COPY src /opt/parser/src
COPY worker.py /opt/parser/worker.py
COPY worker.py /opt/parser/child.py
COPY provenance.json /opt/parser/provenance.json
USER 65532:65532
WORKDIR /opt/parser
ENTRYPOINT ["python", "-I", "-B", "/opt/parser/worker.py"]
