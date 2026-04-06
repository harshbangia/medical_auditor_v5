import boto3
ec2 = boto3.client('ec2',region_name='eu-north-1a')

ec2.stop_instances(InstanceIds=['i-068da88eeb02c099ae'])