from aws_cdk import (
    Stack,
    Duration,
    CfnParameter,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_apigateway as apigateway,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda_event_sources as event_sources,
    aws_kinesis as kinesis
)
from constructs import Construct

class WallboardStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Parameters
        ddb_table_name = CfnParameter(
            self, "DDBTable",
            type="String",
            description="DynamoDB table to create",
            default="ConnectWallboard"
        ).value_as_string

        kinesis_agent_stream = CfnParameter(
            self, "KinesisAgentStream",
            type="String",
            description="Kinesis agent event stream ARN - required to set appropriate Lambda trigger"
        ).value_as_string

        # DynamoDB Table
        table = dynamodb.Table(
            self, "WallboardDynamoDBTable",
            table_name=ddb_table_name,
            partition_key=dynamodb.Attribute(name="Identifier", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="RecordType", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PROVISIONED,
            read_capacity=10,
            write_capacity=10
        )

        # Lambda: Render Wallboard
        render_lambda = lambda_.Function(
            self, "WallboardLambdaRender",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset("../render-wallboard"),
            timeout=Duration.seconds(20),
            environment={"WallboardTable": table.table_name}
        )
        table.grant_read_data(render_lambda)
        render_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["connect:GetCurrentMetricData"],
            resources=["*"]
        ))

        # Lambda: Historical Metrics
        historical_lambda = lambda_.Function(
            self, "WallboardLambdaHistorical",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset("../get-historical-metrics/"),
            timeout=Duration.seconds(20),
            environment={"WallboardTable": table.table_name}
        )
        table.grant_read_write_data(historical_lambda)
        historical_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["connect:GetMetricData"],
            resources=["*"]
        ))

        # EventBridge Rule for Historical Metrics
        events.Rule(
            self, "WallboardHistoricalEvent",
            schedule=events.Schedule.rate(Duration.minutes(1)),
            targets=[targets.LambdaFunction(historical_lambda)]
        )

        # Lambda: Agent Events
        agent_lambda = lambda_.Function(
            self, "WallboardLambdaAgentEvent",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset("../process-agent-event"),
            timeout=Duration.seconds(20),
            environment={"WallboardTable": table.table_name}
        )
        table.grant_read_write_data(agent_lambda)
        agent_lambda.add_event_source(
            event_sources.KinesisEventSource(
                stream=kinesis.Stream.from_stream_arn(self, "KinesisStream", kinesis_agent_stream),
                starting_position=lambda_.StartingPosition.LATEST
            )
        )

        # API Gateway
        api = apigateway.RestApi(
            self, "WallboardAPIGateway",
            deploy_options=apigateway.StageOptions(
                stage_name="prod",
                data_trace_enabled=True
            )
        )

        wallboard_resource = api.root.add_resource("wallboard")
        wallboard_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(render_lambda),
            method_responses=[apigateway.MethodResponse(
                status_code="200",
                response_parameters={"method.response.header.Access-Control-Allow-Origin": False}
            )]
        )
        wallboard_resource.add_cors_preflight(
            allow_origins=["*"],
            allow_methods=["GET", "OPTIONS"]
        )

        # Outputs
        CfnOutput(self, "APIGatewayURL", value=f"{api.url}wallboard/")
        CfnOutput(self, "DynamoDBTableName", value=table.table_name)
