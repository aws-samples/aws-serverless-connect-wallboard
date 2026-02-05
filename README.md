## AWS Serverless Connect Wallboard
## Introduction
`aws-serverless-connect-wallboard` provides a way to build near real-time dashboards for your [Amazon Connect](https://aws.amazon.com/connect) contact center. It was introduced [in this blog post](https://aws.amazon.com/blogs/contact-center/building-a-serverless-contact-center-wallboard-for-amazon-connect/) which has further details about architecture and initial setup. These instructions focus more on how to use the software to create wallboards/dashboards.

First, you will need an Amazon Connect instance up and running. If you wish to track state of agents on your wallboard then you will need to [configure an agent event stream](https://docs.aws.amazon.com/connect/latest/adminguide/agent-event-streams.html) - make note of the ARN.

Next, you will need to deploy the [CloudFormation template](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-cfn.yaml) which will build the Lambda functions, DynamoDB table and API Gateway components for you.

The DynamoDB table holds data that will be displayed on the wallboard (this data is refreshed periodically - see below for more information) and the configuration for the wallboard. You can edit the DynamoDB table directly to change the look of your wallboard but it's probably easier to edit a YAML definition file as described below. You can have multiple wallboard definitions contained in a single DynamoDB table. You can also collect historical and real-time data from multiple Connect instances. If you wish to collect agent events from multiple Connect instances you will need to configure Kinesis to deliver the events to Lambda manually (which will be processed by the [agent event handler](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/process-agent-event.py)).
### Wallboard Configuration
Use the [wallboard import utility](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-import.py) to import a YAML definition file for each wallboard into DynamoDB.

Each definition file will use the following format. Note that many parameters are optional - mandatory ones are marked.
```yaml
WallboardTemplateFormatVersion: 1
Description: <description of this wallboard - for humans only>
Identifier: <Mandatory: unique indentifier for this wallboard>

Defaults:
  TextColor: <default HTML color for text>
  BackgroundColor: <default HTML background for each cell>
  TextSize: <default text size>
  Font: <default font>
  WarningBackgroundColor: <color for cells in "warning" state - default is yellow>
  AlertBackgroundColor: <color for cells in "alert" state - default is red>

Sources:
  - Source: <Mandatory: (local to this wallboard) name of data source>
    Description: <human readable description>
    Reference: <Mandatory: reference to data source in Connect - see below>

Thresholds:
  - Threshold: <Mandatory: unique name of threshold>
    Reference: <Mandatory: name of source created in Sources section - the metric to track>
    WarnBelow: <Either WarnBelow or WarnAbove: value for warning (yellow) when metric is below this value>
    AlertBelow: <Either AlertBelow or AlertAbove: value for alert (red) when metric is below this value>

Calculations:
  - Calculation: <Mandatory: unique name of calculation>
    Formula: <Mandatory: formula for this calcuation - see below>

AgentStates:
  - State: <Mandatory: state name>
    Color: <Mandatory: color for this state>

Rows:
  - Row: <Mandatory: row number for cells following>
    Cells:
    - Cell: <Mandatory: cell number>
      Text: <optional static text to go in cell>
      TextColor: <HTML color for text in this cell>
      BackgroundColor: <HTML background color for this cell>
      TextSize: <font size for this cell>
      Reference: <name of data source or calculation result to put in this cell>
      Format: Time
      ThresholdReference: <name of threshold to apply to this cell>
      Rows: <number of rows to span this cell across - default=1>
      Cells: <number of columns to span this cell across - default=1>
```
The `Format` parameter is used for converting a numeric data point (which should contain an integer specifying seconds) into a HH:MM:SS string. The only format supported currently is `Time`. Any other format type will be ignored. If this paramter is used on string data it will be ignored.
### Browser-based Editing
While you can manually edit your wallboard configuration file you might instead try using the [browser-based editing tool](wallboard-editor.html). An understanding of the topics below is still going to be important but the editor allows for each parameter in the wallboard to be entered and the configuration file is built automatically.

Note that cells can be dragged/dropped to rearrange your wallboard.
### References
When specifying references to data in Amazon Connect the format for each is as follows:
```yaml
xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy:AGENTS_AVAILABLE
```

`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` is the Connect instance identifier. For example, if an instance has an ARN of `arn:aws:connect:us-east-1:111122223333:instance/12345678-1234-1234-1234-123456789012` then you want to use `12345678-1234-1234-1234-123456789012` as the first part of the reference. You can retrieve the Connect instance id directly from the AWS command-line tool by running `aws connect list-instances` and using the `Id` field.

`yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy` is the Connect queue identifier. For example, if a queue has an ARN of `arn:aws:connect:us-east-1:111122223333:instance/12345678-1234-1234-1234-123456789012/queue/87654321-4321-4321-4321-210987654321` then you want to use `87654321-4321-4321-4321-210987654321` as the second part of the reference. You can retrieve the Queue id directly from the command-line by running `aws conenct list-queues --instance-id <Connect instance id>` and using the `Id` field.

Finally, you need to specify the metric that you wish to reference. There are many metrics available and the wallboard will retrieve both real-time and historical metrics for you without you needing to specify which is which. In this version, the wallboard supports the following metrics for each queue:
 - CONTACTS_QUEUED
 - CONTACTS_HANDLED
 - CONTACTS_ABANDONED
 - CONTACTS_CONSULTED
 - CONTACTS_AGENT_HUNG_UP_FIRST
 - CONTACTS_HANDLED_INCOMING
 - CONTACTS_HANDLED_OUTBOUND
 - CONTACTS_HOLD_ABANDONS
 - CONTACTS_TRANSFERRED_IN
 - CONTACTS_TRANSFERRED_OUT
 - CONTACTS_TRANSFERRED_IN_FROM_QUEUE
 - CONTACTS_TRANSFERRED_OUT_FROM_QUEUE
 - CALLBACK_CONTACTS_HANDLED
 - API_CONTACTS_HANDLED
 - CONTACTS_MISSED
 - OCCUPANCY
 - HANDLE_TIME
 - AFTER_CONTACT_WORK_TIME
 - QUEUED_TIME
 - ABANDON_TIME
 - QUEUE_ANSWER_TIME
 - HOLD_TIME
 - INTERACTION_TIME
 - INTERACTION_AND_HOLD_TIME
 - SERVICE_LEVEL
 - AGENTS_AVAILABLE
 - AGENTS_ONLINE
 - AGENTS_ON_CALL
 - AGENTS_STAFFED
 - AGENTS_AFTER_CONTACT_WORK
 - AGENTS_NON_PRODUCTIVE
 - AGENTS_ERROR
 - CONTACTS_IN_QUEUE
 - OLDEST_CONTACT_AGE
 - CONTACTS_SCHEDULED

See the [GetCurrentMetricData API documentation](https://docs.aws.amazon.com/connect/latest/APIReference/API_GetCurrentMetricData.html) for a complete list and description of real-time metrics and the [GetMetric API documentation](https://docs.aws.amazon.com/connect/latest/APIReference/API_GetMetricData.html) for historical metrics.

In a cell, you specify the data that you wish to display by using the `Reference` tag. The data can be a direct reference (i.e. data that is being drawn from Connect directly); it can be the result of a calculation (several metrics that have been somehow modified - see below); or it can be the name of an agent (see below). Note that cell data can also be static - you may want to display a heading for a column or description for a cell.
#### Special note about SERVICE_LEVEL
Thanks to `eaagastr` for pointing this out.

SERVICE_LEVEL is an historical metric that requires an additional parameter: Threshold. This is because the metric is determining what the service level is of a queue and therefore needs the number of seconds that it should evaluate the service level over.

For the time being, there is a small hack into the code so that it doesn't throw an error when SERVICE_LEVEL is requested as a metric. At the top of `get-historical-metrics.py` you'll see a variable which is `ServiceLevelThreshold` and it is set to 60 (seconds). This is static across all queues - you can change this value (between 1 and 604800 inclusive) but you can't set it individually per queue.

In future, this might change - so that you can specify a different threshold per queue. If this is of interest, create a GitHub issue.
### Calculations
Calculations allow you to take metrics and perform simple mathematical operations on them. For example, you may have three queues and wish to display the total number of callers for all three queues. To do this, you could use the following snippet:
```yaml
Sources:
  - Source: Queue1Waiting
    Description: Callers waiting in Queue 1
    Reference: 12345678-1234-1234-1234-123456789012:87654321-4321-4321-4321-210987654321:CONTACTS_IN_QUEUE
  - Source: Queue2Waiting
    Description: Callers waiting in Queue 2
    Source: 12345678-1234-1234-1234-123456789012:87654321-4321-4321-4321-543210987544:CONTACTS_IN_QUEUE
  - Source: Queue3Waiting
    Description: Callers waiting in Queue 3
    Reference: 12345678-1234-1234-1234-123456789012:87654321-4321-4321-4321-568295214776:CONTACTS_IN_QUEUE
    
Calculations:
  - Calculation: TotalCallersWaiting
    Formula: Queue1Waiting+Queue2Waiting+Queue3Waiting

Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Text: Queue 1
      Reference: Queue1Waiting
    - Cell: 2
      Text: Queue 2
      Reference: Queue2Waiting
    - Cell: 3
      Text: Queue 3
      Reference: Queue3Waiting
    - Cell: 4
      Text: Total
      Reference: TotalCallersWaiting
```
Note that simple mathematical functions such as `int()`, `round()`, `min()` and `max()` are supported in calculations.
### Thresholds
Setting thresholds lets you change the background colour of a cell based on the value in that cell or in another cell. You can use this to highlight when (for example) to warn you before a SLA is breached (say, when the maximum waiting time is over two minutes) by setting a threshold at one minute using the `WarnAbove` value and another threshold at two miuntes to show the breach using the `AlertAbove` value.

```yaml
Sources:
  - Source: LongestWaiting
    Description: Longest waiting caller
    Reference: 12345678-1234-1234-1234-123456789012:87654321-4321-4321-4321-210987654321:OLDEST_CONTACT_AGE

Thresholds:
  - Threshold: LongestWaitingWarning
    Reference: LongestWaiting
    WarnAbove: 60
    AlertAvboe: 120

Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Text: Longest waiting
      Reference: LongestWaiting
      ThresholdReference: LongestWaitingWarning
```
You can apply the threshold reference to any other cells even if they do not contain the data that is causing the breach of threshold. That way, you could turn a whole row or column yellow or red (the default colours) to highlight a threshold breach. Thresholds may also reference the output of calculations rather than raw data from Connect.

In addition to `WarnAbove` and `AlertAbove` there are also `WarnBelow` and `AlertBelow` keywords. You may wish to create visible warnings and alerts when metrics are below a certain value. For example, you might want to know when there are less than a specific number of agents available to answer calls.
### Agent states
Make sure that you define colours for each agent state that has been created in Connect. There are no default colours in the wallboard for each state so if a state is detected that doesn't have a colour, the default background colour applies.

Possible states that an agent can be in are:
- Login
- Logout
- Available
- On Contact
- On Hold
- Missed
- Paused
- Rejected
- After Call Work
- Error
- Unknown

You can modify how Connect states [as listed in the documentation](https://docs.aws.amazon.com/connect/latest/adminguide/agent-event-stream-model.html#Contact) are mapped to wallboard states by modifing `process-agent-event.py` at around lines 86-105.

To show an agent state in a cell you do not need to create a reference, the login name of the agent is all that is required:
```yaml
AgentStates:
  - State: Available
    Color: Green
  - State: Offline
    Color: Red
  - State: Work
    Color: Orange
  - State: Lunch
    Color: Yellow

Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Description: Agent state for Alice
      Text: Alice
      Reference: alice
    - Cell: 2
      Description: Agent state for Bob
      Text: Bob
      Reference: bob
    - Cell: 3
      Description: Agent state for Carlos
      Text: Carlos
      Reference: carlos
  - Row: 2
    Cells:
    - Cell: 1
      Description: Agent state for Dave
      Text: Dave
      Reference: dave
    - Cell: 2
      Description: Agent state for Erin
      Text: Erin
      Reference: erin
    - Cell: 3
      Description: Agent state for Eve
      Text: Eve
      Reference: eve
```
In this snippet we display the name of the agent (using the `Text` tag) but the `Reference` points to the login name for that agent. The wallboard will automatically update each cell with the appropriate colour based on each agent's current state in Connect.

In a large contact centre, you may not want to have a static list of agents. You may have different agents on different shifts and that would require you to update the wallboard configuration at shift change. Instead, there are two meta values you can use to display a dynamic list of names.
```yaml
Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Description: Agent state
      Reference: =allagents
    - Cell: 2
      Description: Agent state
      Reference: =allagents
    - Cell: 3
      Text: Carlos
      Reference: =allagents
```
This takes the list of agents from Connect and displays them without you needing to know the names in advanced. Make sure that the `First name` and `Last name` attributes for the agent are filled in as these are used in place of the `Text` tag to display the name of the agent. If you have more agents than cells available then additional agents are not displayed. If you have less agents than cells then the cells are left blank.

It may not be useful to display the state of agents who are not currently logged into the system. Instead you might wish to only display the state of agents that are active.
```yaml
Rows:
  - Row: 1
    Cells:
    - Cell: 1
      Description: Agent state
      Reference: =activeagents
    - Cell: 2
      Description: Agent state
      Reference: =activeagents
    - Cell: 3
      Text: Carlos
      Reference: =activeagents
```
Here the cells will only contain details of agents who are not in a `Logout` state.
### Loading Wallboard Configuration Files 
Once you have your YAML definition file you need to import it into the DynamoDB table. To do this you'll need the [import utility](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-import.py):
```sh
./wallboard-import.py <definition file>
```
### Calling the API
Once imported you can call the API Gateway endpoint that the CloudFormation template configured for you. You can find this in the `Outputs` section of the CloudFormation stack.
```
curl https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/stagename/wallboard?Wallboard=standard
```
This example retrieves the wallboard called `standard` which is the name given to it by the `Identifier` tag in the definition file. You can have multiple definitions coexisting in the wallboard system as long as they have unique identifiers. This allows you to have a single set of data that is displayed differently in many locations. For example, you might have an overarching wallboard shown on a large display and also have less complex wallboards that show a subset of the data on agent desktops in a browser.

By default the Lambda function that renders the wallboard returns a preformatted HTML table. To display this on your secreen, you'll need to write a small piece of Javascript that embeds the wallboard table returned by API Gateway into a web page. Check out [this example page](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-example.html) in this repo for a starting point. Note that you can use CSS to make additional changes to the appearance of the wallboard.

If you'd prefer to render your wallboard using a front-end framework you can request that the API returns a JSON structure instead.
```
curl https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/wallboard?Wallboard=standard&json=true
```
The structure returned will look like this (non-JSON comments embedded for clarity):
```json
{
  "Settings": { # Settings "global" to this wallboard - hints for how to render but not prescriptive
    "TextColour": "black", # Default text colour
    "BackgroundColour": "white", # Default background colour
    "FontSize": "15", # Default font size
    "Font": "sans-serif", # Default font type
    "AlertBackgroundColour": "red", # Colour for cells in "alert"
    "WarningBackgroundColour": "yellow", # Colour for cells in "warn"
    "AgentStateList": { # List of any custom agent states and associated colours
      "Lunch": "yellow"
    }
  },
  "AgentStates": { # List of current agent names and states
    "Alice": "Lunch"
  },
  "WallboardData": { # Data for each cell of the wallboard
    "R1C1": { # Row 1, Column 1
      "Format": { # Formatting hints that may override the "global" settings
        "BackgroundColour": "lightgreen",
        "TextSize": "20"
      },
      "Text": "Agents Available" # Static text to display in the cell
    },
    "R2C1": {
      "Format": {
        "Colour": "blue"
      },
      "Metric": "AGENTS_AVAILABLE", # Name of the metric in this cell
      "Value": "0" # Value for this cell
    }
  }
}
```
It is up to you to determine the appropriate way to parse the data for your purposes but the simplest way is that the metrics are contained within a JSON object called 'WallboardData' and each cell is labelled `R<row number>C<column number>`. The formatting hints (colours and threshold alerts) can be used by you or ignored as you see fit.
### Wallboard Tuning
You may wish to tune specific events in the wallboard system.

Historical metrics are retrieved every minute. This is triggered by CloudWatch Events and can be changed by modifying the `Connect-Wallboard-Historical-Collection` rule. You can also modify the [CloudFormation template](https://github.com/aws-samples/aws-serverless-connect-wallboard/blob/master/wallboard-cfn.yaml) before deployment.

The wallboard configuration is checked every 300 seconds (five minutes) by default. This means that when you update an existing wallboard configuration it may take up to five minutes for the changes to be visible. This can be changed by adding an environment variable called `ConfigTimeout` for the `Connect-Wallboard-Render` and `Connect-Wallboard-Historical-Metrics` Lambda functions and making the value the number of seconds the function should wait before checking for any updated configuration. A small value will mean the functions read from the DynamoDB table more often. This may increase the cost of the solution due to increase database table activity.
### HTML Styles
When rendered as a HTML table there are specific CSS stylesheet classes applied to each element. You can choose to override the default colours, fonts and formatting of the table if you wish.
  - The table will have a stylesheet class of `wallboard-<wallboard name>`. For example, if your wallboard has a name of "primary" then the class will be `wallboard-primary`.
  - Each cell has a class name which is related to the row and column of that cell. The first cell on the first row of the wallboard will have a class of `R1C1` while the third cell on the fourth row will be `R4C3`.

Because each cell is formatted with an inline style, you may have to use the `!important` CSS property to override that style.
## License Summary
This sample code is made available under the MIT-0 license. See the LICENSE file.