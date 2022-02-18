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
from boto3.dynamodb.conditions import Key,Attr
from botocore.exceptions import ClientError
import os
import time
import logging
import datetime

#
# Things to configure
#
DDBTableName          = "ConnectWallboard"
ConfigTimeout         = 300 # How long we wait before grabbing the config from the database
ServiceLevelThreshold = 60  # See note in README.md

#
# Global state
#
LastRun     = 0
DataSources = {}
Data        = {}

Table  = ""
Logger = logging.getLogger()

MetricUnitMapping = {
    "CONTACTS_QUEUED": ["COUNT", "SUM"],
    "CONTACTS_HANDLED": ["COUNT", "SUM"],
    "CONTACTS_ABANDONED": ["COUNT", "SUM"],
    "CONTACTS_CONSULTED": ["COUNT", "SUM"],
    "CONTACTS_AGENT_HUNG_UP_FIRST": ["COUNT", "SUM"],
    "CONTACTS_HANDLED_INCOMING": ["COUNT", "SUM"],
    "CONTACTS_HANDLED_OUTBOUND": ["COUNT", "SUM"],
    "CONTACTS_HOLD_ABANDONS": ["COUNT", "SUM"],
    "CONTACTS_TRANSFERRED_IN": ["COUNT", "SUM"],
    "CONTACTS_TRANSFERRED_OUT": ["COUNT", "SUM"],
    "CONTACTS_TRANSFERRED_IN_FROM_QUEUE": ["COUNT", "SUM"],
    "CONTACTS_TRANSFERRED_OUT_FROM_QUEUE": ["COUNT", "SUM"],
    "CALLBACK_CONTACTS_HANDLED": ["COUNT", "SUM"],
    "CALLBACK_CONTACTS_HANDLED": ["COUNT", "SUM"],
    "API_CONTACTS_HANDLED": ["COUNT", "SUM"],
    "CONTACTS_MISSED": ["COUNT", "SUM"],
    "OCCUPANCY": ["PERCENT", "AVG"],
    "HANDLE_TIME": ["SECONDS", "AVG"],
    "AFTER_CONTACT_WORK_TIME": ["SECONDS", "AVG"],
    "QUEUED_TIME": ["SECONDS", "MAX"],
    "ABANDON_TIME": ["COUNT", "SUM"],
    "QUEUE_ANSWER_TIME": ["SECONDS", "AVG"],
    "HOLD_TIME": ["SECONDS", "AVG"],
    "INTERACTION_TIME": ["SECONDS", "AVG"],
    "INTERACTION_AND_HOLD_TIME": ["SECONDS", "AVG"],
    "SERVICE_LEVEL": ["PERCENT", "AVG"]
  }

def GetConfiguration():
    global LastRun,ConfigTimeout,DDBTableName,Logger,Table,DataSources,UnitMapping
    
    #
    # We only want to retrieve the configuration for the wallboard if we haven't
    # retrieved it recently or it hasn't previously been loaded.
    #
    Logger.debug("Last run at "+str(LastRun)+", timeout is "+str(ConfigTimeout)+", now is "+str(time.time()))
    
    if time.time() < LastRun+ConfigTimeout:
        Logger.debug("  Within timeout period - no config refresh")
        return
    LastRun = time.time()

    #
    # All relevant wallboard information (how it is to be formatted, threshold
    # details, etc.) all have a primary partition key of the name of the
    # wallboard.
    #
    Expression = Attr("RecordType").begins_with("DataSource")
    try:
        Response = Table.scan(FilterExpression=Expression)
    except ClientError as e:
        Logger.error("DynamoDB error: "+e.response["Error"]["Message"])
        return(False)

    if len(Response["Items"]) == 0:
        Logger.error("Did not get any data sources")
        return

    DataSources = {}
    for Item in Response["Items"]:
        if "Name" not in Item:
            Logger.warning("Data source reference not set for "+Item["RecordType"]+" - ignored")
            continue
        
        Metric = Item["Reference"].split(":")[2]
        if Metric not in MetricUnitMapping: continue # Ignore non-historical metrics
        DataSources[Item["Name"]] = Item["Reference"]

    return

def StoreMetric(ConnectARN, QueueARN, MetricName, Value):
    global DataSources,Data,Logger

    SourceString = ConnectARN+":"+QueueARN+":"+MetricName

    for Source in DataSources:
        if DataSources[Source] == SourceString:
            Data[Source] = str(int(Value))
            Logger.debug("Storing "+Data[Source]+" in "+Source)
            return

    Logger.warning("Could not find "+SourceString+" in DataSources")

def GetHistoricalData():
    global Logger,LastRealtimeRun,Data,DataSources,MetricUnitMapping,Data

    Connect = boto3.client("connect")
    
    #
    # Build a list of information we need from the API.
    #
    ConnectList = {}
    for Item in DataSources:
        if Item not in Data: Data[Item] = "0"

        (ConnectARN,QueueARN,Metric) = DataSources[Item].split(":")

        if ConnectARN not in ConnectList: ConnectList[ConnectARN] = {}
        if QueueARN not in ConnectList[ConnectARN]: ConnectList[ConnectARN][QueueARN] = []

        if Metric == 'SERVICE_LEVEL':
            ConnectList[ConnectARN][QueueARN].append({"Name":Metric,"Unit":MetricUnitMapping[Metric][0],"Statistic":MetricUnitMapping[Metric][1],"Threshold":{"Comparison":"LT","ThresholdValue":ServiceLevelThreshold}})
        else:
            ConnectList[ConnectARN][QueueARN].append({"Name":Metric,"Unit":MetricUnitMapping[Metric][0],"Statistic":MetricUnitMapping[Metric][1]})

    FiveMinuteMark = datetime.datetime.now().minute-datetime.datetime.now().minute%5
    
    #
    # Now call the API for each Connect instance we're interested in.
    #
    for Instance in ConnectList:
        Logger.debug("Retrieving historical data from "+Instance)
        
        MetricList = []
        for Queue in ConnectList[Instance]:
            MetricList += ConnectList[Instance][Queue]
            
        try:
            Response = Connect.get_metric_data(
                           InstanceId=Instance,
                           StartTime=datetime.datetime.now().replace(hour=0, minute=0, second=0),
                           EndTime=datetime.datetime.now().replace(minute=FiveMinuteMark, second=0),
                           Groupings=["QUEUE"],
                           Filters={"Queues":list(ConnectList[Instance].keys())},
                           HistoricalMetrics=MetricList)
        except Exception as e:
            Logger.error("Failed to get historical data: "+str(e))
            continue

        if "MetricResults" not in Response: continue
        for Collection in Response["MetricResults"]:
            QueueARN   = Collection["Dimensions"]["Queue"]["Id"]

            for Metric in Collection["Collections"]:
                MetricName  = Metric["Metric"]["Name"]
                MetricValue = Metric["Value"]
                StoreMetric(Instance, QueueARN, MetricName, MetricValue)

def WriteData():
    global Data,DDBTableName

    Dynamo = boto3.client("dynamodb")

    for Item in Data:
        DDBOutput = {}
        DDBOutput["Identifier"] = {"S":"Data"}
        DDBOutput["RecordType"] = {"S":Item}
        DDBOutput["Value"]      = {"S":Data[Item]}

        try:
            Dynamo.put_item(TableName=DDBTableName, Item=DDBOutput)
        except ClientError as e:
            Logger.error("DDB put error: "+e.response["Error"]["Message"])

 
def lambda_handler(event, context):
    global Table,DDBTableName,Logger
    
    logging.basicConfig()
    Logger.setLevel(logging.INFO)
    
    if os.environ.get("WallboardTable") is not None: DDBTableName  = os.environ.get("WallboardTable")
    if os.environ.get("ConfigTimeout")  is not None: ConfigTimeout = os.environ.get("ConfigTimeout")
    
    Table = boto3.resource("dynamodb").Table(DDBTableName)

    GetConfiguration()
    GetHistoricalData()
    WriteData()

    return
