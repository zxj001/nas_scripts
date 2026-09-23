"""Docker Engine plus Compose; docker group membership needs a new login."""

import grp
import os

from setup_tasks.common import apt

# Docker Desktop links its CLI here, and it is often not on PATH yet.
MAC_APP = "/Applications/Docker.app"
MAC_APP_CLI = MAC_APP + "/Contents/Resources/bin/docker"


def command(ctx):
    if ctx.have("docker"):
        return "docker"
    if ctx.profile == "macos" and ctx.path(MAC_APP_CLI).exists():
        return str(ctx.path(MAC_APP_CLI))
    return None


def compose(ctx):
    cli = command(ctx)
    return bool(cli) and ctx.test(cli, "compose", "version", codes=tuple(range(256)))


def group():
    try:
        return grp.getgrnam("docker")
    except KeyError:
        return None


def member(ctx):
    entry = group()
    return entry is not None and ctx.user in entry.gr_mem


def this_login():
    entry = group()
    return entry is not None and entry.gr_gid in os.getgroups()


def service(ctx):
    return all(
        ctx.test("systemctl", check, "--quiet", "docker", codes=(0, 1, 3, 4))
        for check in ("is-enabled", "is-active")
    )


def relogin(ctx):
    return ctx.result(
        "deferred",
        "docker group membership changed; current login has not changed",
        "Log out and back in to use docker without sudo, then resume",
    )


def probe(ctx, task):
    if ctx.profile == "macos":
        # Docker Desktop's engine runs only after the app is opened and its
        # terms are accepted, which setup cannot do for the operator.
        if compose(ctx) and ctx.test(command(ctx), "info", codes=tuple(range(256))):
            return ctx.result("satisfied")
        if ctx.path(MAC_APP).exists():
            return ctx.result(
                "manual",
                "Docker Desktop is installed but its engine is not running",
                "Open Docker.app, accept the terms, then rerun --only docker",
            )
        # Another engine (OrbStack, Colima) owns this docker CLI; never
        # install Docker Desktop over it.
        if compose(ctx):
            return ctx.result(
                "manual",
                "a docker CLI is installed but its engine is not running",
                "Start your Docker engine, then rerun --only docker",
            )
        if command(ctx):
            return ctx.result(
                "manual",
                "the installed docker CLI has no Compose plugin",
                "Add Compose to your Docker engine, then rerun --only docker",
            )
        return ctx.result("pending")
    if not (ctx.have("dockerd") and compose(ctx) and service(ctx)):
        return ctx.result("pending")
    if os.geteuid() == 0:
        return ctx.result("satisfied")
    if not member(ctx):
        return ctx.result("pending", f"{ctx.user} is not in the docker group")
    if not this_login():
        return relogin(ctx)
    return ctx.result("satisfied")


def apply(ctx, task):
    if ctx.profile == "macos":
        ctx.run("brew", "install", "--cask", "docker-desktop")
        return probe(ctx, task)
    missing = []
    if not ctx.have("dockerd"):
        missing.append("docker.io")
    if not ctx.have("docker"):
        missing.append("docker-cli")
    if not compose(ctx):
        docker_ce = ctx.run(
            "dpkg-query", "-W", "-f=${Status}", "docker-ce", allowed=(0, 1), split=True
        ).stdout
        if docker_ce == "install ok installed":
            # Debian's docker.io and docker-compose conflict with Docker's own packages.
            return ctx.result(
                "manual",
                "Docker CE is installed without the Compose plugin",
                "Install docker-compose-plugin from Docker's apt repository and retry",
            )
        missing.append("docker-compose")
    if missing:
        apt(ctx, *missing)
    ctx.run("systemctl", "enable", "--now", "docker", privileged=True)
    if os.geteuid() == 0:
        return ctx.result("changed")
    if not member(ctx):
        ctx.run("/usr/sbin/usermod", "-aG", "docker", ctx.user, privileged=True)
    if not this_login():
        return relogin(ctx)
    return ctx.result("changed")
