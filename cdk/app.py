#!/usr/bin/env python3
import aws_cdk as cdk
from cdk_wallboard.wallboard_stack import WallboardStack

app = cdk.App()
WallboardStack(app, "ConnectWallboardStack")
app.synth()
