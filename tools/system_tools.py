import datetime
from pydantic import Field
import os
import signal
import sys
import threading
import time

class System:
    def exit_agent(self, message: str = Field(description="Your final goodbye message to the user.")):
        """Exits the agent and terminates the program.
        You MUST provide a 'message' argument with your final goodbye message to the user.
        CRITICAL RULE: Do NOT use this tool unless the user EXPLICITLY says 'bye', 'goodbye', 'exit', 'quit', OR answers 'yes' when you ask if that is all. 
        Never assume a conversation is over just because the user asks you to remember something for the future or if they say 'okay'.
        """
        print(f"\nAI: {message}\n", file=sys.stderr)
        
        def delayed_exit():
            time.sleep(0.5) 
            os.kill(os.getppid(), signal.SIGINT) 
            
        threading.Thread(target=delayed_exit, daemon=True).start()
        return message


    def get_current_date_and_time(self) -> str:
        """Returns the current date and time. Returns in the following format: YYYY-MM-DD HH:MM:SS. 
        Good for using in email templates when you need to insert current date and time or find emails by date."""
        now = datetime.datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S")


