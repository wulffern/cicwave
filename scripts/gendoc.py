#!/usr/bin/env python3
"""Expand <!--run_output:-->/<!--run_image:-->/<!--cat:--> directives in a
markdown template into a rendered docs page.

Modeled on cicsim's tests/gendoc.py: a directive block looks like

    <!--run_output:
    run: cicwave --help
    -->

and is replaced by the command plus its captured stdout/stderr, wrapped in
a bash code fence. ``run_image`` additionally copies the file named by
``output_image`` into ``<outdir>/assets/`` and emits a markdown image
link. Everything outside a directive block is copied through unchanged.
"""

import os
import re
import shutil
import subprocess

import click
import yaml


def do_cmd_with_return(cmd):
    result = subprocess.run(
        cmd, shell=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return result.stdout


class GenDoc:

    def run_image(self, buff, odir):
        obj = yaml.safe_load(buff)
        ss = "```bash\n" + obj["run"] + "\n```\n\n"
        iurl = obj["output_image"]
        ourl = odir + os.path.sep + "assets" + os.path.sep + iurl

        subprocess.run(obj["run"], shell=True, check=True)
        shutil.copy2(iurl, ourl)

        ss += "![](/cicwave/assets/%s)" % iurl
        return ss

    def cat(self, buff, odir):
        obj = yaml.safe_load(buff)
        finame = obj["file"]
        language = obj.get("language", "")

        with open(finame) as fi:
            ss = fi.read()

        if language == "markdown":
            return ss + "\n\n"
        return finame + ":\n" + "```%s\n" % language + ss + "\n```\n\n"

    def run_output(self, buff, odir):
        obj = yaml.safe_load(buff)
        ss = "```bash\n" + obj["run"] + "\n```\n\n"
        ss += "```bash\n" + do_cmd_with_return(obj["run"]) + "\n```\n\n"
        return ss

    def cli(self, finame, foname):
        odir = os.path.dirname(foname)
        is_cmd = False
        cmd = ""
        buff = ""
        with open(finame) as fi, open(foname, "w") as fo:
            for line in fi:
                if re.search("^-->", line):
                    is_cmd = False
                    handler = getattr(self, cmd, None)
                    if handler is None:
                        raise RuntimeError(
                            "Don't know how to support directive '%s'" % cmd)
                    fo.write(handler(buff, odir))
                    cmd = ""
                    buff = ""
                    continue

                if is_cmd:
                    buff += line
                    continue

                m = re.search("^<!--([^:]+):", line)
                if m:
                    buff = ""
                    is_cmd = True
                    cmd = m.group(1)
                    continue

                fo.write(line)


@click.command()
@click.argument("finame")
@click.argument("foname")
def cli(finame, foname):
    GenDoc().cli(finame, foname)


if __name__ == "__main__":
    cli()
