https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application?platform=linux

You must add a runner to GitHub before you can configure the self-hosted runner application as a service. For more information, see Adding self-hosted runners.
For Linux systems that use systemd, you can use the svc.sh script that is created after successfully adding the runner to install and manage using the application as a service.

On the runner machine, open a shell in the directory where you installed the self-hosted runner application. Use the commands below to install and manage the self-hosted runner service.

Installing the service

Stop the self-hosted runner application if it is currently running.

Install the service with the following command:

sudo ./svc.sh install
Alternatively, the command takes an optional user argument to install the service as a different user.

./svc.sh install USERNAME
Starting the service

Start the service with the following command:

sudo ./svc.sh start
Note

On Debian-based Linux systems (such as Debian or Ubuntu) with needrestart enabled, you can prevent needrestart from restarting the runner service during a workflow job by configuring it to ignore the runner service. Run the following command:

echo '$nrconf{override_rc}{qr(^actions\.runner\..+\.service$)} = 0;' | sudo tee /etc/needrestart/conf.d/actions_runner_services.conf
Checking the status of the service

Check the status of the service with the following command:

sudo ./svc.sh status
For more information on viewing the status of your self-hosted runner, see Monitoring and troubleshooting self-hosted runners.

Stopping the service

Stop the service with the following command:

sudo ./svc.sh stop
Uninstalling the service

Stop the service if it is currently running.

Uninstall the service with the following command:

sudo ./svc.sh uninstall
Customizing the self-hosted runner service

If you don't want to use the above default systemd service configuration, you can create a customized service or use whichever service mechanism you prefer. Consider using the serviced template at actions-runner/bin/actions.runner.service.template as a reference. If you use a customized service, the self-hosted runner service must always be invoked using the runsvc.sh entry point.