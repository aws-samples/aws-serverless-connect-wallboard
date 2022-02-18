#!/usr/bin/python

#
# Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
import base64
import json
import os
import logging

DDBTableName = "ConnectWallboard"

Logger = logging.getLogger()

def SaveStateToDDB(Username, FullAgentName, AgentARN, State):
    global DDBTableName,Logger
    
    Data = {}
    Data["Identifier"]    = {"S":"Data"}
    Data["RecordType"]    = {"S":Username}
    Data["Value"]         = {"S":State}
    Data["AgentARN"]      = {"S":AgentARN}
    Data["FullAgentName"] = {"S":FullAgentName}
    
    Dynamo = boto3.client("dynamodb")
    try:
        Dynamo.put_item(TableName=DDBTableName, Item=Data)
    except ClientError as e:
        Logger.error("DDB put error: "+e.response["Error"]["Message"])

def SaveStateUsingARN(AgentARN, State):
    global DDBTableName,Logger
    
    Table = boto3.resource("dynamodb").Table(DDBTableName)
    try:
        # Scan the table looking for the ARN
        Expression = Attr("AgentARN").eq(AgentARN)
        Response = Table.scan(FilterExpression=Expression)
    except:
        Logger.error("DDB scan error: "+e.response["Error"]["Message"])
        return
    
    if len(Response["Items"]) > 0:
        Logger.debug("AgentARN: "+AgentARN+" = "+Response["Items"][0]["RecordType"])
        SaveStateToDDB(Response["Items"][0]["RecordType"], Response["Items"][0]["FullAgentName"], AgentARN, State)
    
def lambda_handler(event, context):
    global DDBTableName,Logger
    
    logging.basicConfig()
    Logger.setLevel(logging.INFO)

    if os.environ.get("WallboardTable") is not None: DDBTableName = os.environ.get("WallboardTable")
        
    for RawPayload in event["Records"]:
        AgentEvent = json.loads(base64.b64decode(RawPayload["kinesis"]["data"]))
        EventType = AgentEvent["EventType"]
        AgentARN = AgentEvent["AgentARN"]
        Logger.debug("Event type: "+EventType+" AgentARN: "+AgentARN)
        
        if EventType == "LOGIN": # We don't really need to do this but just in case...
            SaveStateUsingARN(AgentARN, "Login")   
            continue
        if EventType == "LOGOUT":
            SaveStateUsingARN(AgentARN, "Logout")   
            continue
        if EventType == "STATE_CHANGE":
            State     = AgentEvent["CurrentAgentSnapshot"]["AgentStatus"]["Name"]
            AgentName = AgentEvent["CurrentAgentSnapshot"]["Configuration"]["FirstName"]+" "+AgentEvent["CurrentAgentSnapshot"]["Configuration"]["LastName"]
            Username  = AgentEvent["CurrentAgentSnapshot"]["Configuration"]["Username"]
            Logger.debug("Agent: "+AgentName+" ("+Username+") State: "+State)
            if len(AgentName) == 1: Logger.warning("Expected first and last name of agent but didn't get it in the event.")

            SaveStateToDDB(Username, AgentName, AgentARN, State)
            continue
        if EventType == "HEART_BEAT":
            # Not sure what to do here yet
            continue
        
        Logger.warning("Unknown event type: "+EventType)
        
    return
