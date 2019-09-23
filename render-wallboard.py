#!/usr/bin/python

#
# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
import string
import re

#
# Things to configure
#
DDBTableName    = "ConnectWallboard"
ConfigTimeout   = 300 # How long we wait before grabbing the config from the database
RealtimeTimeout = 5 # How long before in between polling the real-time API

DefaultSettings = Settings = {
    "AlertBackgroundColour": "red", 
    "WarningBackgroundColour": "yellow", 
    "TextColour": "black", 
    "Font": "sans-serif",
    "BackgroundColour": "lightgrey"
}

#
# Global state
#
LastRun         = 0
LastRealtimeRun = 0
Settings        = {}
Cells           = {}
Thresholds      = {}
AgentStates     = {}
Data            = {}
Calculations    = {}
DataSources     = {}
NextAgent       = 0
SortedAgentList = []
FullAgentNames  = {}

Table           = ""
Logger          = logging.getLogger()

MetricUnitMapping = {
    "AGENTS_AVAILABLE": "COUNT",
    "AGENTS_ONLINE": "COUNT",
    "AGENTS_ON_CALL": "COUNT",
    "AGENTS_STAFFED": "COUNT",
    "AGENTS_AFTER_CONTACT_WORK": "COUNT",
    "AGENTS_NON_PRODUCTIVE": "COUNT",
    "AGENTS_ERROR": "COUNT",
    "CONTACTS_IN_QUEUE": "COUNT",
    "OLDEST_CONTACT_AGE": "SECONDS",
    "CONTACTS_SCHEDULED": "COUNT"                                 
  }

def GetConfiguration(WallboardName):
    global LastRun,ConfigTimeout,DefaultSettings,DDBTableName,Logger,Table,Settings,Cells,Thresholds,AgentStates,Calculations,DataSources
    
    #
    # We only want to retrieve the configuration for the wallboard if we haven't
    # retrieved it recently or it hasn't previously been loaded.
    #
    GetConfig = False
    if WallboardName not in Settings:
        LastRun = time.time()
        GetConfig = True
        Logger.debug("No config loaded for "+WallboardName+" - retrieving")
    else:
        Logger.debug("Last run at "+str(LastRun)+", timeout is "+str(ConfigTimeout)+", now is "+str(time.time()))
    
        if time.time() > LastRun+ConfigTimeout:
            LastRun = time.time()
            GetConfig = True
            Logger.debug("  Wallboard config needs refreshing")
        else:
            Logger.debug("  Within timeout period - no config refresh")
    
    if not GetConfig: return(True)

    #
    # All relevant wallboard information (how it is to be formatted, threshold
    # details, etc.) all have a primary partition key of the name of the
    # wallboard.
    #
    try:
        Response = Table.query(KeyConditionExpression=Key("Identifier").eq(WallboardName))
    except ClientError as e:
        Logger.error("DynamoDB error: "+e.response["Error"]["Message"])
        return(False)

    if len(Response["Items"]) == 0:
        Logger.error("Did not get any configuration for wallboard "+WallboardName)
        return(False)

    LocalSettings     = DefaultSettings
    LocalThresholds   = {}
    LocalCells        = {}
    LocalAgentStates  = {}
    LocalCalculations = {}
    LocalDataSources  = {}
    for Item in Response["Items"]:
        if Item["RecordType"] == "Settings":
            for Config in Item:
                LocalSettings[Config] = Item[Config]
        elif Item["RecordType"][:11] == "Calculation":
            if "Formula" not in Item:
                Logger.warning("Formula not set for "+Item["RecordType"]+" in wallboard " +WallboardName+" - ignored")
                continue
            LocalCalculations[Item["Name"]] = Item["Formula"]
        elif Item["RecordType"][:4] == "Cell":
            if "Address" not in Item:
                Logger.warning("Cell address not set for "+Item["RecordType"]+" in wallboard "+WallboardName+" - ignored")
                continue
            LocalCells[Item["Address"]] = Item
        elif Item["RecordType"][:9] == "Threshold":
            if "Name" not in Item:
                Logger.warning("Threshold name not set for "+Item["RecordType"]+" in wallboard "+WallboardName+" - ignored")
                continue
            LocalThresholds[Item["Name"]] = Item
        elif Item["RecordType"][:10] == "AgentState":
            if "StateName" not in Item:
                Logger.warning("Agent state name not set for "+Item["RecordType"]+" in wallboard "+WallboardName+" - ignored")
                continue
            LocalAgentStates[Item["StateName"]] = Item["BackgroundColour"]
        elif Item["RecordType"][:10] == "DataSource":
            if "Name" not in Item:
                Logger.warning("Data source reference not set for "+Item["RecordType"]+" in wallboard "+WallboardName+" - ignored")
                continue
            
            Metric = Item["Reference"].split(":")[2]
            if Metric not in MetricUnitMapping: continue # Ignore non real-time metrics
            LocalDataSources[Item["Name"]] = Item["Reference"]

    Settings[WallboardName]     = LocalSettings
    Cells[WallboardName]        = LocalCells
    Thresholds[WallboardName]   = LocalThresholds
    AgentStates[WallboardName]  = LocalAgentStates
    Calculations[WallboardName] = LocalCalculations
    DataSources[WallboardName]  = LocalDataSources
    
    return(True)

def GetData():
    global Logger,Data,NextAgent,SortedAgentList,FullAgentNames
    
    SortedAgentList = []
    NextAgent       = 0

    #
    # All data retrieved from other sources is stored in the DDB table with
    # the primary partition key of "Data" and a primary sort key of the name
    # of the value that has been stored.
    # We could get back numerical data (stored as a string) or agent state
    # details.
    #
    try:
        Response = Table.query(KeyConditionExpression=Key("Identifier").eq("Data"))
    except ClientError as e:
        Logger.error("DynamoDB error: "+e.response["Error"]["Message"])
        return
    
    if len(Response["Items"]) == 0:
        Logger.error("Did not get any data from DynamoDB")
        return

    for Item in Response["Items"]:
        Data[Item["RecordType"]] = Item["Value"]
        if "AgentARN" in Item:
            SortedAgentList.append(Item["RecordType"])
            if "FullAgentName" in Item:
                FullAgentNames[Item["RecordType"]] = Item["FullAgentName"]
        
    #
    # We want the agents in alphabetical order
    #
    SortedAgentList.sort()
    
def StoreMetric(ConnectARN, QueueARN, MetricName, Value):
    global DataSources,Data,Logger

    SourceString = ConnectARN+":"+QueueARN+":"+MetricName

    for Wallboard in DataSources:
        for Source in DataSources[Wallboard]:
            if DataSources[Wallboard][Source] == SourceString:
                Data[Source] = str(int(Value))
                Logger.debug("Storing "+Data[Source]+" in "+Source)
                return

    Logger.warning("Could not find "+SourceString+" in DataSources")

def GetRealtimeData():
    global Logger,LastRealtimeRun,Data,DataSources,MetricUnitMapping

    #
    # We only want to poll the real-time API every so often.
    #
    Logger.debug("Last real-time poll at "+str(LastRealtimeRun)+", timeout is "+str(RealtimeTimeout)+", now is "+str(time.time()))
    
    if time.time() < LastRealtimeRun+RealtimeTimeout: return
    LastRealtimeRun = time.time()

    Connect = boto3.client("connect")
    
    #
    # Even though data sources are defined per wallboard we will always retrieve
    # all of them each time as they may be cross-referenced on other wallboards.
    #
    # First build a list of information we need from the API.
    #
    ConnectList = {}
    for WallboardName in DataSources:
        for Item in DataSources[WallboardName]:
            if Item not in Data: Data[Item] = "0"

            (ConnectARN,QueueARN,Metric) = DataSources[WallboardName][Item].split(":")
 
            if ConnectARN not in ConnectList: ConnectList[ConnectARN] = {}
            if QueueARN not in ConnectList[ConnectARN]: ConnectList[ConnectARN][QueueARN] = []
            ConnectList[ConnectARN][QueueARN].append({"Name":Metric, "Unit":MetricUnitMapping[Metric]})

    #
    # Now call the API for each Connect instance we're interested in.
    #
    for Instance in ConnectList:
        Logger.debug("Retrieving real-time data from "+Instance)
        
        MetricList = []
        for Queue in ConnectList[Instance]:
            MetricList += ConnectList[Instance][Queue]

        try:
            Response = Connect.get_current_metric_data(
                           InstanceId=Instance,
                           Groupings=["QUEUE"],
                           Filters={"Queues":list(ConnectList[Instance].keys())},
                           CurrentMetrics=MetricList)
        except Exception as e:
            Logger.error("Failed to get real-time data: "+str(e))
            continue

        if "MetricResults" not in Response: continue
        for Collection in Response["MetricResults"]:
            QueueARN   = Collection["Dimensions"]["Queue"]["Id"]

            for Metric in Collection["Collections"]:
                MetricName  = Metric["Metric"]["Name"]
                MetricValue = Metric["Value"]
                StoreMetric(Instance, QueueARN, MetricName, MetricValue)

def DoCalculation(WallboardName, Reference):
    global Logger,Data,Calculations
    
    Result = "0" # All values are stored as strings when they come out of DDB

    #
    # Split the calculation based on mathemetical operators
    #
    CalcArray = re.split("(\+|\*|\-|\/|\(|\))", Calculations[WallboardName][Reference])

    # Substitute in the values for the labels in the calculation
    #
    Index = 0
    for Index in range(0, len(CalcArray)):
        if CalcArray[Index] in string.punctuation: continue
        if CalcArray[Index][0] in string.digits: continue
    
        if CalcArray[Index] in Data:
            CalcArray[Index] = Data[CalcArray[Index]]
        else:
            Logger.warning("Calc: Could not find reference "+CalcArray[Index])
            CalcArray[Index] = "0"

    CalcString = "".join(CalcArray)
    Logger.debug("Calculation for "+Reference+": "+Calculations[WallboardName][Reference]+" -> "+CalcString)
    
    try:
        Result = str(eval(CalcString))
    except Exception as e:
        Logger.error("Could not eval "+Reference+" ["+Calculations[WallboardName][Reference]+"] -> ["+CalcString+"] "+str(e))
        
    return(Result)
    
def CheckThreshold(WallboardName, ThresholdReference):
    global Settings,Data,Thresholds,Logger,Calculations
    
    #
    # For the given data reference, check for any threshold details and then
    # return the right colour (which will be used for the cell background when
    # displayed). We have warning thresholds (above and below) and error
    # thresholds (above and below).
    #
    Colour = ""

    if ThresholdReference not in Thresholds[WallboardName]:
        Logger.warning("Threshold reference "+ThresholdReference+" does not exist for wallboard "+WallboardName)
        return(Colour)

    Threshold = Thresholds[WallboardName][ThresholdReference]
    if "Reference" not in Threshold:
        Logger.warning("No data reference present in threshold "+ThresholdReference+ "for wallboard "+WallboardName)
        return(Colour)

    if Threshold["Reference"] not in Data:
        if Threshold["Reference"] in Calculations:
            Data[Threshold["Reference"]] = DoCalculation(WallboardName, Threshold["Reference"])
        else:
            Logger.warning("Data reference "+Threshold["Reference"]+" in threshold "+ThresholdReference+" does not exist for wallboard "+WallboardName)
            return(Colour)

    if "WarnBelow" in Threshold:
        if int(Data[Threshold["Reference"]]) < int(Threshold["WarnBelow"]): Colour = Settings[WallboardName]["WarningBackgroundColour"]
    if "AlertBelow" in Threshold:
        if int(Data[Threshold["Reference"]]) < int(Threshold["AlertBelow"]): Colour = Settings[WallboardName]["AlertBackgroundColour"]
    if "WarnAbove" in Threshold:
        if int(Data[Threshold["Reference"]]) > int(Threshold["WarnAbove"]): Colour = Settings[WallboardName]["WarningBackgroundColour"]
    if "AlertAbove" in Threshold:
        if int(Data[Threshold["Reference"]]) > int(Threshold["AlertAbove"]): Colour = Settings[WallboardName]["AlertBackgroundColour"]

    return(Colour)

def GetNextAgent(GetActive):
    global SortedAgentList,NextAgent,FullAgentNames
    
    #
    # When we need to display a list of all agents currently active, this
    # function returns the names one-by-one so that the caller can fill in the
    # cells in the wallboard table.
    #
    AgentName = ""
    HTML      = ""
    
    if NextAgent >= len(SortedAgentList): return(HTML, "") # No more agents - cell is blank

    if not GetActive: # Return the next agent whether active in the system or not
        AgentName = SortedAgentList[NextAgent]
        NextAgent += 1
    else: # Return the next active agent
        while NextAgent < len(SortedAgentList):
            AgentState = Data[SortedAgentList[NextAgent]]
            if len(AgentState) == 0 or AgentState == "Logout":
                NextAgent += 1
                continue
            
            AgentName = SortedAgentList[NextAgent]
            NextAgent += 1
            break
        
    if len(AgentName) == 0: return(HTML, "") # No agent found - cell is blank
    
    if AgentName in FullAgentNames: # Just in case we didn't find a full name for this agent
        HTML += "<div class=\"text\">"+FullAgentNames[AgentName]+"</div>"
    HTML += "<div class=\"data\">"+Data[AgentName]+"</div>"

    return(HTML, Data[AgentName]) # Return the state so we can set the cell background colour

def RenderCell(WallboardName, Row, Column):
    global AgentStates,Thresholds,Logger,Data,Calculations
    
    #
    # Given a particular cell, figure out the right colours and cell contents.
    # A cell may contain static text, a number or agent state derived directly
    # from the data read from the DDB table, or it may be a calculation we need
    # to perform. Also need to ensure that thresholds are checked for numerical
    # values where present.
    #
    Address      = "R"+str(Row)+"C"+str(Column)
    HTML         = ""
    AgentDetails = ""
    
    if Address not in Cells[WallboardName]: return(HTML)
    Cell = Cells[WallboardName][Address]
    LocalStates = AgentStates[WallboardName]

    Style = [] 
    Style.append("border: 1px solid black; padding: 5px;")
    if "TextColour" in Cell: Style.append("color: "+Cell["TextColour"]+";")
    if "TextSize"   in Cell: Style.append("font-size: "+Cell["TextSize"]+"px;")

    Background = ""
    if "Reference" in Cell:
        State = ""
        if Cell["Reference"] in Calculations[WallboardName]: # We need to calculate this one
            Data[Cell["Reference"]] = DoCalculation(WallboardName, Cell["Reference"])
        elif Cell["Reference"].lower() in Data: # Data already exists
            State = Data[Cell["Reference"]]
        elif Cell["Reference"] == "=allagents": # Any agent at all
            (AgentDetails, State) = GetNextAgent(False)
        elif Cell["Reference"] == "=activeagents": # Active agents only
            (AgentDetails, State) = GetNextAgent(True)

        if len(State) > 0:
            State = State.lower()
            if State in LocalStates:
                Background = LocalStates[State]

    if "ThresholdReference" in Cell:
        NewBackground = CheckThreshold(WallboardName, Cell["ThresholdReference"])
        if len(NewBackground) > 0: Background = NewBackground
        
    if len(Background) == 0:
        if "BackgroundColour" in Cell: Background = Cell["BackgroundColour"]
    if len(Background) > 0: Style.append("background: "+Background+";")

    Tag = "R"+str(Row)+"C"+str(Column)
    HTML += "<td label=\""+Tag+"\" class=\""+Tag+"\""
    if "Rows"     in Cell: HTML += " rowspan=\""+Cell["Rows"]+"\""
    if "Columns"  in Cell: HTML += " colspan=\""+Cell["Columns"]+"\""
    if len(Style) > 0: HTML += " style=\""+" ".join(Style)+"\""
    HTML += ">"

    if "Text" in Cell: HTML += "<div class=\"text\">"+Cell["Text"]+"</div>"
    if "Reference" in Cell:
        if Cell["Reference"] in Data:
            HTML += "<div class=\"data\">"+Data[Cell["Reference"]]+"</div>"
        elif Cell["Reference"] == "=allagents" or Cell["Reference"] == "=activeagents":
            HTML += AgentDetails
        else:
            Logger.warning("Data reference "+Cell["Reference"]+" in cell "+Address+" does not exist for wallboard "+WallboardName)

    HTML += "</td>"
    return(HTML)

def RenderHTML(WallboardName):
    global Settings

    #
    # Build the containing table for the wallboard and then render each cell
    # according to the wallboard configuration.
    #
    LocalSettings = Settings[WallboardName]
    HTML = ""

    HTML += "<table label=\"ConnectWallboard"+LocalSettings["Identifier"].replace(" ", "")+"\""
    HTML += " style=\"border: 1px solid black; border-collapse: collapse; margin-left: auto; margin-right: auto; text-align: center;"
    if "TextColour"       in LocalSettings: HTML += " color: "+LocalSettings["TextColour"]+";"
    if "BackgroundColour" in LocalSettings: HTML += " background: "+LocalSettings["BackgroundColour"]+";"
    if "TextSize"         in LocalSettings: HTML += " font-size: "+LocalSettings["TextSize"]+"px;"
    if "Font"             in LocalSettings: HTML += " font-family: "+LocalSettings["Font"]+";"
    HTML += "\" class=\"wallboard-"+WallboardName+"\">\n"

    for Row in range(1, int(LocalSettings["Rows"])+1):
        HTML += " <tr>"
        for Column in range(1, int(LocalSettings["Columns"])+1):
            HTML += RenderCell(WallboardName, Row, Column)
        HTML += "</tr>\n"

    HTML += "</table>\n"

    return(HTML)

def lambda_handler(event, context):
    global Table,DDBTableName,Logger
    
    logging.basicConfig()
    Logger.setLevel(logging.INFO)
    
    if os.environ.get("WallboardTable") is not None: DDBTableName  = os.environ.get("WallboardTable")
    if os.environ.get("ConfigTimeout")  is not None: ConfigTimeout = os.environ.get("ConfigTimeout")
    
    Table = boto3.resource("dynamodb").Table(DDBTableName)
    GetData()

    Response = {}
    Response["statusCode"] = 200
    Response["headers"]    = {"Access-Control-Allow-Origin": "*"}

    if str(type(event["queryStringParameters"])).find("dict") == -1 or "Wallboard" not in event["queryStringParameters"]:
        Response["body"] = "<div class=\"error\">No wallboard name specified</div>"
        return(Response)

    WallboardName = event["queryStringParameters"]["Wallboard"]
    if GetConfiguration(WallboardName):
        GetRealtimeData()
        HTML = RenderHTML(WallboardName)
    else:
        HTML = "<div class=\"error\">Wallboard "+WallboardName+" not found</div>"

    Response["body"] = HTML
    return(Response)