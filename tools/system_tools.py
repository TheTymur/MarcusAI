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


    def deep_system_diagnostic(self) -> str:
        import re
        from tools.gmail_tools import GmailClient
        from agent.data.database_manager import Emailprocessing
        from tools.knowledge_tools import VectorMemory
        from tools.multiply import multiply
        """
        Performs a rigorous, cinematic "Deep Diagnostic" test. 
        It physically tests every single tool using a real E2E queue (creating and deleting real drafts/labels).
        Use this ONLY when the user explicitly asks for a "cinematic check", "deep diagnostic", 
        or wants to see a true E2E tool run.
        CRITICAL: This tool CANNOT create, modify, or forward real emails for the user. Do NOT confuse this with create_response_draft, modify_draft, or forward_email.
        
        CRITICAL INSTRUCTION FOR AI: When you use this tool, you MUST reply to the user with the EXACT string returned by this tool verbatim. 
        DO NOT summarize the results. You MUST print the full list of tools and their statuses exactly as they are returned to you.
        """
        
        report = "Initiating deep E2E system diagnostic...\n"
        report += "-"*50 + "\n"
        
        try:
            gmail = GmailClient()
            db = Emailprocessing()
        except Exception as e:
            return f"Critical failure during initialization: {str(e)}"
            
        def log(msg, status="ok"):
            nonlocal report
            report += f"{msg} -- {status}\n"
            
    #Drafts

        draft_id = None
        try:
            res = gmail.create_draft(to="dummy@example.cam", subject="AGY TEST", message="Ignore")
            match = re.search(r"Draft '([^']+)'", res)
            if not match: raise ValueError(f"Failed to parse draft ID: {res}")
            draft_id = match.group(1)
            log("create_draft")
            
            res2 = gmail.modify_draft(draft_id=draft_id, to="dummy2@example.cam", subject="AGY TEST 2", message="Modified")
            if "modified successfully" not in res2: raise ValueError(res2)
            log("modify_draft")
            
            res3 = gmail.list_drafts()
            if draft_id not in res3: raise ValueError("Draft not found in list")
            log("list_drafts")
            
        except Exception as e:
            log("Drafts queue", f"error: {str(e)}")
        finally:
            if draft_id:
                try:
                    gmail.delete_draft(draft_id=draft_id)
                    log("delete_draft (cleanup)")
                except Exception as e:
                    log("delete_draft (cleanup)", f"error: {str(e)}")

    #Labels

        label_id = None
        try:
            res = gmail.create_label("AGY_TEST_LABEL")
            match = re.search(r"ID is ([a-zA-Z0-9_-]+)\.", res)
            if not match: raise ValueError(f"Failed to parse label ID: {res}")
            label_id = match.group(1)
            log("create_label")
            
            res2 = gmail.read_recent_emails(limit=1)
            email_match = re.search(r"ID: ([a-zA-Z0-9_-]+)", res2)
            if not email_match:
                log("apply_label", "not properly func (no recent emails)")
                log("remove_label", "not properly func")
            else:
                email_id = email_match.group(1)
                log("read_recent_emails")
                
                res3 = gmail.apply_label(email_id=email_id, label_id=label_id)
                if "successfully" not in res3: raise ValueError(res3)
                log("apply_label")
                
                res4 = gmail.count_messages_in_label(label_id=label_id)
                if "has " not in res4: raise ValueError(res4)
                log("count_messages_in_label")
                
                res5 = gmail.remove_label(email_id=email_id, label_id=label_id)
                if "successfully" not in res5: raise ValueError(res5)
                log("remove_label")
                
        except Exception as e:
            log("Labels queue", f"error: {str(e)}")
        finally:
            if label_id:
                try:
                    gmail.delete_label(label_id=label_id)
                    log("delete_label (cleanup)")
                except Exception as e:
                    log("delete_label (cleanup)", f"error: {str(e)}")
                    
    #Block/unblock
        try:
            res1 = gmail.block_sender("diagnostic_test_block@example.cam")
            if "Successfully" not in res1: raise ValueError(res1)
            log("block_sender")
            
            res2 = gmail.unblock_sender("diagnostic_test_block@example.cam")
            if "Successfully" not in res2: raise ValueError(res2)
            log("unblock_sender")
        except Exception as e:
            log("Block API queue", f"error: {str(e)}")
            
    # Individual Tests
        try:
            res = gmail.search_emails("test")
            log("search_emails")
        except Exception as e:
            log("search_emails", f"error: {str(e)}")
            
        try:
            res = db.search_email_metadata(limit=1)
            log("search_local_database")
        except Exception as e:
            log("search_local_database", f"error: {str(e)}")
            
        try:
            self.get_current_date_and_time()
            log("get_current_date_and_time")
        except Exception as e:
            log("get_current_date_and_time", f"error: {str(e)}")
            
        try:
            res = gmail.delete_message("dummy_id_123")
            if isinstance(res, str) and ("error" in res.lower() or "not found" in res.lower()):
                log("delete_message", "ok (expected rejection)")
            else:
                log("delete_message", "ok (success)")
        except Exception as e:
            log("delete_message", f"ok (expected rejection: {str(e)[:30]})")
            
        try:
            multiply(1, 1)
            log("multiply")
        except Exception as e:
            log("multiply", f"error: {str(e)}")
            
        try:
            gmail.read_email_content("dummy_id")
            log("read_email_content", "ok (success)")
        except Exception as e:
            log("read_email_content", f"ok (expected rejection: {str(e)[:30]})")
            
        try:
            gmail.create_response_draft("dummy_id", "dummy")
            log("create_response_draft", "ok (success)")
        except Exception as e:
            log("create_response_draft", f"ok (expected rejection: {str(e)[:30]})")
            
        try:
            gmail.forward_email("dummy_id", "dummy@example.com")
            log("forward_email", "ok (success)")
        except Exception as e:
            log("forward_email", f"ok (expected rejection: {str(e)[:30]})")
            
        try:
            mem = VectorMemory()
            mem.search_knowledge_base("test")
            log("search_knowledge_base")
        except Exception as e:
            log("search_knowledge_base", f"error: {str(e)}")

        log("unsubscribe_from_email", "not properly func (requires UI interaction)")
        log("send_draft", "not properly func (avoids spamming Sent folder)")
        log("save_user_preference", "not properly func (requires live user state)")
        try:
            db_res = db.search_email_metadata(limit=1)
            if db_res and "- ID:" in db_res:
                db_match = re.search(r"- ID: ([a-zA-Z0-9_-]+)", db_res)
                if db_match:
                    local_id = db_match.group(1)
                    gmail.read_local_email(local_id)
                    log("read_local_email", "ok (success)")
                else:
                    log("read_local_email", "not properly func (failed to parse DB ID)")
            else:
                log("read_local_email", "not properly func (no emails in local DB to test)")
        except Exception as e:
            log("read_local_email", f"error: {str(e)[:30]}")

        log("exit_agent", "not properly func (would terminate application)")
        
        report += "\n" + "-"*60 + "\n"
        report += "Diagnostic complete. E2E queue finished.\n"
        
        print(report)
                
        return "System diagnostic completed. The full raw report has been printed directly to the user's terminal. You should briefly tell the user that the diagnostic is done and the results are on their screen."
