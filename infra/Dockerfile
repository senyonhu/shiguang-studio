FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS isolate-builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libcap-dev libseccomp-dev pkg-config git ca-certificates
RUN git clone https://github.com/ioi/isolate.git /tmp/isolate-v2 && \
    cd /tmp/isolate-v2 && git checkout 8f185bb37f3f23e29b33b0c7727c91c13429abe3 && \
    make isolate LDFLAGS=-static
FROM judge0/judge0:1.13.1@sha256:6b5d6a66aa19a8e878a52ea3c6a560afc1086734d96e2885b561fd5c6018f082
USER root
# Official Isolate v2.7, statically linked for Judge0's older userspace.
# Preserve namespace, cgroup memory/CPU, seccomp, and wall-clock isolation.
COPY --from=isolate-builder /tmp/isolate-v2/isolate /usr/local/bin/isolate
COPY isolate.conf /usr/local/etc/isolate
COPY cgroup-entrypoint.sh /usr/local/bin/cgroup-entrypoint.sh
RUN chmod 755 /usr/local/bin/cgroup-entrypoint.sh && chmod 4755 /usr/local/bin/isolate
# Isolate 2 always accounts CPU over the cgroup; the old toggles were removed.
RUN sed -i 's/--no-cg-timing//g; s/--cg-timing//g' /api/app/jobs/isolate_job.rb
ENTRYPOINT ["/usr/local/bin/cgroup-entrypoint.sh"]
CMD ["./scripts/server"]
