#!/usr/bin/python

#
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

import yaml
import sys
import signal
import os
import time
import boto3
from botocore.exceptions import NoCredentialsError

#
# Global variables
#
Dynamo       = boto3.client('dynamodb')
DDBTableName = os.environ.get('WallboardTable', 'ConnectWallboard')

Settings     = {}
Calculations = []
Thresholds   = []
AgentStates  = {}
Cells        = []
DataSources  = []
MaxColumns   = 0
MaxRows      = 0

#
# Function definitions
#

def Interrupt(signal, frame):
    print('\n')
    sys.exit(0)

def UpdateSettings(Config, Settings):
    if 'Defaults' in Config:
        Defaults = Config['Defaults']
        if 'TextColour'              in Defaults: Settings['TextColour']              = {'S':Defaults['TextColour']}
        if 'TextColor'               in Defaults: Settings['TextColour']              = {'S':Defaults['TextColor']}
        if 'BackgroundColour'        in Defaults: Settings['BackgroundColour']        = {'S':Defaults['BackgroundColour']}
        if 'BackgroundColor'         in Defaults: Settings['BackgroundColour']        = {'S':Defaults['BackgroundColor']}
        if 'TextSize'                in Defaults: Settings['TextSize']                = {'S':str(Defaults['TextSize'])}
        if 'Font'                    in Defaults: Settings['Font']                    = {'S':Defaults['Font']}
        if 'WarningBackgroundColour' in Defaults: Settings['WarningBackgroundColour'] = {'S':Defaults['WarningBackgroundColour']}
        if 'WarningBackgroundColor'  in Defaults: Settings['WarningBackgroundColour'] = {'S':Defaults['WarningBackgroundColor']}
        if 'AlertBackgroundColour'   in Defaults: Settings['AlertBackgroundColour']   = {'S':Defaults['AlertBackgroundColour']}
        if 'AlertBackgroundColor'    in Defaults: Settings['AlertBackgroundColour']   = {'S':Defaults['AlertBackgroundColor']}

def GetCalculations(CalculationsConfig):
    Calculations = []

    for Calc in CalculationsConfig:
        if 'Formula' not in Calc:
            print(f'Missing formula calculation in {Calc["ReferenceName"]}')
            sys.exit(1)

        Calculations.append({'Name':{'S':str(Calc['Calculation'])}, 'Formula':{'S':Calc['Formula']}})

    return Calculations

def GetThresholds(ThresholdConfig):
    Thresholds = []

    for Threshold in ThresholdConfig:
        if 'Reference' not in Threshold:
            print(f'Missing reference in threshold {Threshold["ReferenceName"]}')
            sys.exit(1)
        if 'WarnBelow' not in Threshold and 'AlertBelow' not in Threshold and \
           'WarnAbove' not in Threshold and 'AlertAbove' not in Threshold:
            print(f'No actual threshold set in threshold {Threshold["ReferenceName"]}')
            sys.exit(1)

        Item = {}
        Item['Name']      = {'S':str(Threshold['Threshold'])} # Stringify just in case this is a numeric
        Item['Reference'] = {'S':Threshold['Reference']}
        if 'WarnBelow'  in Threshold: Item['WarnBelow']  = {'S':str(Threshold['WarnBelow'])}
        if 'AlertBelow' in Threshold: Item['AlertBelow'] = {'S':str(Threshold['AlertBelow'])}
        if 'WarnAbove'  in Threshold: Item['WarnAbove']  = {'S':str(Threshold['WarnAbove'])}
        if 'AlertAbove' in Threshold: Item['AlertAbove'] = {'S':str(Threshold['AlertAbove'])}

        Thresholds.append(Item)

    return Thresholds

def GetAgentStates(AgentConfig):
    StateColours = []

    for Item in AgentConfig:
        State = {}
        State['StateName'] = {'S':Item['State'].lower()}
        if 'Colour' in Item: State['BackgroundColour'] = {'S':Item['Colour'].lower()}
        if 'Color'  in Item: State['BackgroundColour'] = {'S':Item['Color'].lower()}
        StateColours.append(State)

    return StateColours

def GetDataSources(SourceConfig):
    Sources = []
    Connect = boto3.client('connect')
    Boto3Warning = False


    for Item in SourceConfig:
        SourceInfo = {}
        SourceInfo['Name']      = {'S':Item['Source']}
        SourceInfo['Reference'] = {'S':Item['Reference']}
        Sources.append(SourceInfo)

        #
        # Just in case, check the references given and see if we can confirm
        # if the queue and Connect instance exist. This helps if there is a
        # typo in the definition file.
        # 
        try:
            (InstanceId,QueueId,Metric) = Item['Reference'].split(':')
        except Exception as e:
            print(f'Check formatting of {Item["Source"]}: {e}')
            continue

        try:
            QueueResponse = Connect.list_queues(InstanceId=InstanceId)
        except AttributeError:
            if not Boto3Warning:
                print('Could not get boto3 response - are you using the latest version?')
                print(' -> Unable to verify if the reference values are correct')
                Boto3Warning = True
            continue
        except NoCredentialsError:
            print('FATAL: No AWS credentials could be found')
            sys.exit(1)
        except Exception as e:
            print(f'{Item["Source"]}: The InstanceId may be incorrect: {InstanceId}')
            print(e)
            continue
        
        QueueList = []
        for Queue in QueueResponse['QueueSummaryList']:
            QueueList.append(Queue['Id'])

        if QueueId not in QueueList:
            print(f'{Item["Source"]}: The QueueId may be incorrect: {QueueId}')

    return Sources

def GetCells(RowConfig):
    Cells   = []
    Columns = 0
    Rows    = 0

    for Row in RowConfig:
        if 'Row' not in Row:
            print('Missing row number')
            sys.exit(1)
        if 'Cells' not in Row:
            print(f'Missing cell definitions on row {Row["Row"]}')
            sys.exit(1)

        #
        # We capture the maximum number of columns because it makes
        # our lives easier to know this during the render function
        #
        if len(Row['Cells']) > Columns: Columns = len(Row['Cells'])

        for Cell in Row['Cells']:
            if 'Cell' not in Cell:
                print(f'Missing cell number on row {Row["Row"]}')
                sys.exit(1)

            if int(Row['Row']) > Rows: Rows = int(Row['Row'])

            Item = {}
            Item['Address'] = {'S':f'R{Row["Row"]}C{Cell["Cell"]}'}

            if 'Text'               in Cell: Item['Text']               = {'S':Cell['Text']}
            if 'Reference'          in Cell: Item['Reference']          = {'S':Cell['Reference']}
            if 'TextColour'         in Cell: Item['TextColour']         = {'S':Cell['TextColour']}
            if 'TextColor'          in Cell: Item['TextColour']         = {'S':Cell['TextColor']}
            if 'BackgroundColour'   in Cell: Item['BackgroundColour']   = {'S':Cell['BackgroundColour']}
            if 'BackgroundColor'    in Cell: Item['BackgroundColour']   = {'S':Cell['BackgroundColor']}
            if 'TextSize'           in Cell: Item['TextSize']           = {'S':str(Cell['TextSize'])}
            if 'ThresholdReference' in Cell: Item['ThresholdReference'] = {'S':Cell['ThresholdReference']}
            if 'Rows'               in Cell: Item['Rows']               = {'S':str(Cell['Rows'])}
            if 'Cells'              in Cell: Item['Cells']              = {'S':str(Cell['Cells'])}
            if 'Format'             in Cell: Item['Format']             = {'S':str(Cell['Format'])}

            Cells.append(Item)

    return Cells,Rows,Columns

def SaveToDynamoDB(WallboardName,Records,RecordType):
    global Dynamo

    Count  = 0

    for Item in Records:
        Item['Identifier'] = {'S':WallboardName}
        if RecordType != 'Settings':
            Item['RecordType'] = {'S':f'{RecordType}{Count}'}
            Count += 1
        else:
            Item['RecordType'] = {'S':RecordType}

        try:
            Dynamo.put_item(TableName=DDBTableName, Item=Item)
        except NoCredentialsError:
            print('FATAL: No AWS credentials could be found')
            sys.exit(1)
        except Exception as e:
            print(f'DynamoDB error: {e}')

def CreateDDBTable():
    global Dynamo

    try:
        Response = Dynamo.describe_table(TableName=DDBTableName)
    except NoCredentialsError:
        print('FATAL: No AWS credentials could be found')
        sys.exit(1)
    except:
        Table = Dynamo.create_table(TableName=DDBTableName,
                                    KeySchema=[{'AttributeName':'Identifier', 'KeyType':'HASH'},
                                               {'AttributeName':'RecordType', 'KeyType':'RANGE'}],
                                    AttributeDefinitions=[{'AttributeName':'Identifier', 'AttributeType':'S'}, {'AttributeName':'RecordType', 'AttributeType':'S'}],
                                    BillingMode='PAY_PER_REQUEST')

        Table = Dynamo.describe_table(TableName=DDBTableName)
        while Table['Table']['TableStatus'] != 'ACTIVE':
            print(f'Waiting for table creation. State: {Table["Table"]["TableStatus"]}')
            time.sleep(10)
            Table = Dynamo.describe_table(TableName=DDBTableName)

#
# Mainline code
#
# Basic setup and argument check
#

signal.signal(signal.SIGINT, Interrupt)

if len(sys.argv) != 2:
    print('Usage: wallboard-import.py wallboarddefinition.yaml')
    sys.exit(1)

#
# Read the YAML file
#

with open(sys.argv[1]) as Input:
    try:
        Config = yaml.safe_load(Input)
    except yaml.YAMLError as e:
        print(e)
        sys.exit(1)

CreateDDBTable()

Settings['WarningBackgroundColour'] = {'S':'Yellow'}
Settings['AlertBackgroundColour']   = {'S':'Red'}

#
# Input validation
#

if 'Identifier' not in Config:
    print('Missing Identifier tag')
    sys.exit(1)

if 'Rows' not in Config:
    print('Missing row definitions')
    sys.exit(1)

#
# Somewhat validated now - let's parse the input
#

UpdateSettings(Config, Settings)
if 'Calculations' in Config: Calculations = GetCalculations(Config['Calculations'])
if 'Thresholds'   in Config: Thresholds   = GetThresholds(Config['Thresholds'])
if 'AgentStates'  in Config: AgentStates  = GetAgentStates(Config['AgentStates'])
if 'Sources'      in Config: DataSources  = GetDataSources(Config['Sources'])
(Cells, MaxRows, MaxColumns) = GetCells(Config['Rows'])

if MaxRows == 0:
    print('No rows were found')
    sys.exit(1)
if MaxColumns == 0:
    print('No cells were found')
    sys.exit(1)

Settings['Columns'] = {'S':str(MaxColumns)}
Settings['Rows']    = {'S':str(MaxRows)}

SaveToDynamoDB(Config['Identifier'], [Settings],   'Settings')
SaveToDynamoDB(Config['Identifier'], Thresholds,   'Threshold')
SaveToDynamoDB(Config['Identifier'], Calculations, 'Calculation')
SaveToDynamoDB(Config['Identifier'], Cells,        'Cell')
SaveToDynamoDB(Config['Identifier'], AgentStates,  'AgentState')
SaveToDynamoDB(Config['Identifier'], DataSources,  'DataSource')
