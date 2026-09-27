import datetime
from pydantic import Field
class System:
    def exit_agent(self, message: str = Field(description="Your final goodbye message to the user.")):
        """Exits the agent if the user told it to exit or if the user is done with the agent. 
        You MUST provide a 'message' argument with your final goodbye message to the user.
        Do NOT use this tool unless the user explicitly tells you to exit.
        """
        import os
        import signal
        print(f"\nAI: {message}\n")
        # Terminating parental process to exit main agent
        os.kill(os.getppid(), signal.SIGTERM)
        return "Exiting..."


    def get_current_date_and_time(self) -> str:
        """Returns the current date and time. Returns in the following format: YYYY-MM-DD HH:MM:SS. 
        Good for using in email templates when you need to insert current date and time or find emails by date."""
        now = datetime.datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S")


