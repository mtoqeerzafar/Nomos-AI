"use client";

import React, { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MessageSquare, Plus, Send, Menu, Bot, User as UserIcon, Loader2, FileText, X, Paperclip } from "lucide-react";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  attachments?: {id: string, name: string}[];
  sources?: string[];
};

type Thread = {
  thread_id: string;
  title: string;
};

// Use dynamic hostname to support network access
const API_BASE = typeof window !== 'undefined' 
  ? `http://${window.location.hostname}:8000/api` 
  : "http://localhost:8000/api";
  
const TENANT_ID = "default_tenant"; 

export default function ChatApp() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<{id: string, name: string, status: 'uploading' | 'done' | 'error'}[]>([]);
  
  // Sidebar state
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch threads on mount
  useEffect(() => {
    fetchThreads();
  }, []);

  // Fetch messages when active thread changes
  useEffect(() => {
    if (activeThreadId) {
      fetchMessages(activeThreadId);
    } else {
      setMessages([]);
      setUploadedFiles([]);
    }
  }, [activeThreadId]);

  // Smart Auto-scroll: Only scroll to bottom if user is near the bottom
  useEffect(() => {
    if (chatContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
      // If within 150px of bottom, auto-scroll (user is reading the latest)
      if (scrollHeight - scrollTop - clientHeight < 150) {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      }
    } else {
      // If chatContainerRef isn't mounted, fallback to scrolling EndRef
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  const fetchThreads = async () => {
    try {
      const res = await fetch(`${API_BASE}/threads`, {
        headers: { "x-tenant-id": TENANT_ID }
      });
      if (res.ok) {
        const data = await res.json();
        setThreads(data);
        if (data.length > 0 && !activeThreadId) {
          setActiveThreadId(data[0].thread_id);
        }
      }
    } catch (e) {
      console.error("Failed to fetch threads", e);
    }
  };

  const fetchMessages = async (threadId: string) => {
    try {
      const res = await fetch(`${API_BASE}/threads/${threadId}/messages`, {
        headers: { "x-tenant-id": TENANT_ID }
      });
      if (res.ok) {
        const data = await res.json();
        setMessages(data);
      }
    } catch (e) {
      console.error("Failed to fetch messages", e);
    }
  };

  const handleNewChat = () => {
    setActiveThreadId(null);
    setMessages([]);
    setUploadedFiles([]);
    setInputValue("");
    // We intentionally don't POST to /threads here. We wait for the first message!
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    let currentThreadId = activeThreadId;
    if (!currentThreadId) {
      try {
        const res = await fetch(`${API_BASE}/threads`, {
          method: "POST",
          headers: { 
            "Content-Type": "application/json",
            "x-tenant-id": TENANT_ID
          },
          body: JSON.stringify({ title: `Upload: ${files[0].name}${files.length > 1 ? ` + ${files.length - 1} files` : ""}` })
        });
        if (res.ok) {
          const data = await res.json();
          setThreads(prev => {
            if (prev.some(t => t.thread_id === data.thread_id)) return prev;
            return [data, ...prev];
          });
          setActiveThreadId(data.thread_id);
          currentThreadId = data.thread_id;
        } else {
          throw new Error("Failed to create thread implicitly");
        }
      } catch (err) {
        console.error(err);
        return;
      }
    }

    // Process all files concurrently
    const uploadPromises = Array.from(files).map(async (file) => {
      const fileId = Date.now().toString() + "-" + Math.random().toString().substring(2, 8);
      setUploadedFiles(prev => [...prev, { id: fileId, name: file.name, status: 'uploading' }]);
      
      try {
        const urlRes = await fetch(`${API_BASE}/upload-url?filename=${encodeURIComponent(file.name)}`);
        if (!urlRes.ok) throw new Error("Failed to get upload URL");
        const { upload_url, s3_key } = await urlRes.json();

        const s3Res = await fetch(upload_url, {
          method: "PUT",
          body: file,
        });
        if (!s3Res.ok) throw new Error("Failed to upload to storage");

        const processRes = await fetch(`${API_BASE}/process?s3_key=${encodeURIComponent(s3_key)}&thread_id=${currentThreadId}`, {
          method: "POST",
          headers: { "x-tenant-id": TENANT_ID }
        });
        if (!processRes.ok) throw new Error("Failed to start processing");
        const { job_id } = await processRes.json();

        setUploadedFiles(prev => prev.map(f => f.id === fileId ? { ...f, id: job_id, status: 'done' } : f));
      } catch (err: any) {
        console.error(err);
        setUploadedFiles(prev => prev.map(f => f.id === fileId ? { ...f, status: 'error' } : f));
      }
    });

    await Promise.all(uploadPromises);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const sendMessage = async () => {
    if (!inputValue.trim() || isGenerating) return;

    let currentThreadId = activeThreadId;
    if (!currentThreadId) {
      try {
        const title = inputValue.substring(0, 30) || "New Conversation";
        const res = await fetch(`${API_BASE}/threads`, {
          method: "POST",
          headers: { 
            "Content-Type": "application/json",
            "x-tenant-id": TENANT_ID
          },
          body: JSON.stringify({ title: title })
        });
        if (res.ok) {
          const data = await res.json();
          setThreads(prev => {
            if (prev.some(t => t.thread_id === data.thread_id)) return prev;
            return [data, ...prev];
          });
          setActiveThreadId(data.thread_id);
          currentThreadId = data.thread_id;
        } else {
          throw new Error("Failed to create thread implicitly");
        }
      } catch (err) {
        console.error(err);
        return;
      }
    }

    const attachmentsToSend = uploadedFiles.filter(f => f.status === 'done').map(f => ({id: f.id, name: f.name}));

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content: inputValue,
      attachments: attachmentsToSend
    };
    
    setMessages(prev => [...prev, userMessage]);
    setInputValue("");
    setUploadedFiles([]);
    setIsGenerating(true);

    const tempAiMessageId = "temp-" + Date.now();
    setMessages(prev => [...prev, { id: tempAiMessageId, role: "assistant", content: "" }]);

    // Force scroll to bottom immediately upon sending a message
    setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 100);

    try {
      const res = await fetch(`${API_BASE}/query/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-tenant-id": TENANT_ID
        },
        body: JSON.stringify({
          question: userMessage.content,
          thread_id: currentThreadId,
          attachments: attachmentsToSend
        })
      });

      if (!res.body) throw new Error("No response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");

      let currentAssistantMessage = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n\n");
        
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const dataStr = line.replace("data: ", "");
            try {
              const data = JSON.parse(dataStr);
              const eventType = data.type || data.node;
              
              if (eventType === "final" || eventType === "answer_end") {
                if (data.answer) {
                  currentAssistantMessage = data.answer;
                }
                const sources = data.sources || [];
                setMessages(prev => prev.map(msg => 
                  msg.id === tempAiMessageId ? { ...msg, content: currentAssistantMessage, sources } : msg
                ));
              } else if (eventType === "token") {
                currentAssistantMessage += data.content;
                setMessages(prev => prev.map(msg => 
                  msg.id === tempAiMessageId ? { ...msg, content: currentAssistantMessage } : msg
                ));
              } else if (eventType === "answer_start") {
                currentAssistantMessage = "";
                setMessages(prev => prev.map(msg => 
                  msg.id === tempAiMessageId ? { ...msg, content: "" } : msg
                ));
              } else if (eventType === "status") {
                if (!currentAssistantMessage) {
                  setMessages(prev => prev.map(msg => 
                    msg.id === tempAiMessageId ? { ...msg, content: `*${data.message}*` } : msg
                  ));
                }
              } else if (eventType === "metric") {
                console.log("Metric received:", data);
              } else {
                // Fallback for old schema
                if (!currentAssistantMessage && data.node) {
                  setMessages(prev => prev.map(msg => 
                    msg.id === tempAiMessageId ? { ...msg, content: `*Processing step: ${data.node}...*` } : msg
                  ));
                }
              }
            } catch (err) {
              console.error("Failed to parse SSE", err);
            }
          }
        }
      }
    } catch (e) {
      console.error("Streaming error", e);
      setMessages(prev => prev.map(msg => 
        msg.id === tempAiMessageId ? { ...msg, content: "Sorry, an error occurred while generating the response." } : msg
      ));
    } finally {
      setIsGenerating(false);
      fetchMessages(currentThreadId);
      fetchThreads(); // Refresh thread list in case title was updated
    }
  };

  const handleDeleteDocument = async (jobId: string, msgId: string) => {
    // Optimistically remove from UI first
    setMessages(prev =>
      prev.map(msg =>
        msg.id === msgId
          ? { ...msg, attachments: (msg.attachments || []).filter(a => a.id !== jobId) }
          : msg
      )
    );

    // Then delete from backend (MinIO + Qdrant + DB)
    try {
      const res = await fetch(`${API_BASE}/documents/${encodeURIComponent(jobId)}`, {
        method: "DELETE",
        headers: { "x-tenant-id": TENANT_ID }
      });
      if (!res.ok) {
        console.error("Failed to delete document from backend", await res.text());
      }
    } catch (err) {
      console.error("Error deleting document:", err);
    }
  };

  const renderInputArea = (isCentered: boolean) => (
    <div className={`input-container ${isCentered ? "centered" : ""}`}>
      {/* Attachments Area */}
      {uploadedFiles.length > 0 && (
        <div style={{ display: "flex", gap: "10px", marginBottom: "12px", flexWrap: "wrap" }}>
          {uploadedFiles.map(file => (
            <div key={file.id} className="file-chip">
              {file.status === 'uploading' ? (
                <Loader2 size={16} className="spin" color="var(--accent-color)" />
              ) : file.status === 'error' ? (
                <FileText size={16} color="var(--danger-color)" />
              ) : (
                <FileText size={16} color="var(--success-color)" />
              )}
              <span style={{ fontSize: "0.85rem", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {file.name}
              </span>
              <X 
                size={14} 
                style={{ cursor: "pointer", opacity: 0.7 }} 
                onClick={() => handleRemoveStagedFile(file.id)}
              />
            </div>
          ))}
        </div>
      )}

      <div className="input-box">
        <input 
          type="file" 
          ref={fileInputRef} 
          style={{ display: "none" }} 
          onChange={handleFileUpload}
          accept=".pdf,.txt,.md,.csv,.json"
          multiple
        />
        
        {/* Top: Text Input */}
        <input 
          type="text" 
          className="text-input"
          placeholder="How can I help you today?" 
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              sendMessage();
            }
          }}
          disabled={isGenerating}
        />
        
        {/* Bottom: Toolbar */}
        <div className="input-toolbar">
          <button 
            className="attach-btn"
            onClick={() => fileInputRef.current?.click()}
            title="Upload Document"
          >
            <Plus size={20} />
          </button>
          
          <button 
            className="send-btn" 
            onClick={sendMessage}
            disabled={!inputValue.trim() || isGenerating}
          >
            <Send size={16} />
          </button>
        </div>
      </div>
      <div style={{ textAlign: "center", fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: "12px" }}>
        AI can make mistakes. Please verify critical information.
      </div>
    </div>
  );

  return (
    <div className="app-container">
      {/* Sidebar */}
      <div className={`sidebar ${!isSidebarOpen ? "closed" : ""}`}>
        <div style={{ padding: "16px", display: "flex", alignItems: "center", gap: "10px" }}>
          <Menu 
            size={24} 
            color="var(--text-secondary)" 
            style={{ cursor: "pointer", marginRight: "4px", flexShrink: 0 }} 
            onClick={() => setIsSidebarOpen(false)}
          />
          <span style={{ fontWeight: 700, fontSize: "1.15rem", color: "var(--text-primary)", letterSpacing: "-0.5px" }}>
            Nomos AI
          </span>
        </div>
        
        <button className="new-chat-btn" onClick={handleNewChat}>
          <Plus size={18} /> New Chat
        </button>
        
        <div style={{ flex: 1, overflowY: "auto", padding: "10px 0" }}>
          <div style={{ padding: "0 16px 8px 16px", fontSize: "0.8rem", color: "var(--text-secondary)", fontWeight: 500 }}>
            Recent
          </div>
          {threads.map(t => (
            <div 
              key={t.thread_id} 
              className={`thread-item ${activeThreadId === t.thread_id ? "active" : ""}`}
              onClick={() => {
                setActiveThreadId(t.thread_id);
                setUploadedFiles([]);
                if (window.innerWidth < 768) setIsSidebarOpen(false); // Auto-close on mobile
              }}
            >
              <MessageSquare size={14} style={{ opacity: 0.7 }} />
              <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {t.title}
              </span>
            </div>
          ))}
        </div>
        
        {/* Profile / Bottom Section */}
        <div style={{ padding: "16px", borderTop: "1px solid var(--border-color)", display: "flex", alignItems: "center", gap: "12px" }}>
           <div style={{ width: "32px", height: "32px", borderRadius: "50%", background: "#444", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.9rem", fontWeight: "bold" }}>
             MT
           </div>
           <div style={{ display: "flex", flexDirection: "column" }}>
             <span style={{ fontSize: "0.9rem" }}>Muhammad Toqeer</span>
             <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>Enterprise Plan</span>
           </div>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="main-chat">
        <div className="chat-header">
          {!isSidebarOpen && (
             <Menu 
               size={24} 
               color="var(--text-secondary)" 
               style={{ cursor: "pointer", marginRight: "16px" }} 
               onClick={() => setIsSidebarOpen(true)}
             />
          )}
          <h3 className="header-title">
            {activeThreadId ? threads.find(t => t.thread_id === activeThreadId)?.title || "" : "Nomos AI"}
          </h3>
        </div>

        {messages.length === 0 ? (
          <div className="empty-state-container">
             <h1 className="serif-heading" style={{ fontSize: "2.5rem", marginBottom: "6px", color: "var(--text-primary)", fontWeight: 700, letterSpacing: "-0.5px" }}>
                Nomos AI
             </h1>
             <p style={{ color: "var(--text-secondary)", marginBottom: "32px", fontSize: "1rem", fontWeight: 400 }}>
                The Statutory Intelligence Engine
             </p>
             {renderInputArea(true)}
          </div>
        ) : (
          <>
            <div className="messages-container" ref={chatContainerRef}>
              {messages.map((msg) => (
                <div key={msg.id} className={`message-wrapper ${msg.role}`}>
                  <div style={{ display: "flex", flexDirection: "row", gap: "16px", maxWidth: "100%", width: "100%" }}>
                    
                    {/* Avatar */}
                    {msg.role === "assistant" && (
                      <div style={{ 
                        width: "32px", height: "32px", borderRadius: "6px", display: "flex", alignItems: "center", justifyContent: "center",
                        background: "var(--accent-color)", flexShrink: 0, marginTop: "4px"
                      }}>
                        <Bot size={20} color="white" />
                      </div>
                    )}
                    
                    <div className="message-bubble" style={{ flex: 1, padding: msg.role === "assistant" ? "0 12px" : "12px 16px" }}>
                      
                      {/* Name Header */}
                      <div style={{ fontWeight: 600, fontSize: "0.9rem", marginBottom: "8px", color: msg.role === "user" ? "var(--text-secondary)" : "var(--accent-color)" }}>
                         {msg.role === "user" ? "You" : "Nomos AI"}
                      </div>
                      
                      {msg.attachments && msg.attachments.length > 0 && (
                        <div style={{ display: "flex", gap: "10px", marginBottom: "12px", flexWrap: "wrap" }}>
                          {msg.attachments.map(att => (
                            <div key={att.id} className="file-chip" style={{ background: "var(--bg-secondary)", border: "1px solid var(--success-color)", position: "relative" }}>
                              <FileText size={16} color="var(--success-color)" />
                              <span style={{ fontSize: "0.85rem", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-primary)" }}>
                                {att.name}
                              </span>
                              <button
                                title="Remove document"
                                onClick={() => handleDeleteDocument(att.id, msg.id)}
                                style={{
                                  background: "none",
                                  border: "none",
                                  cursor: "pointer",
                                  padding: "0",
                                  display: "flex",
                                  alignItems: "center",
                                  opacity: 0.75,
                                  color: "var(--danger-color)",
                                  lineHeight: 1
                                }}
                              >
                                <X size={13} />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      
                      {msg.role === "assistant" ? (
                        <div className="markdown">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {msg.content}
                          </ReactMarkdown>

                          {/* Sources */}
                          {msg.sources && msg.sources.length > 0 && (
                            <div style={{ marginTop: "12px", borderTop: "1px solid var(--border-color)", paddingTop: "12px" }}>
                              <span style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "8px", display: "block" }}>
                                Sources
                              </span>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                                {msg.sources.map((src, idx) => (
                                  <div key={idx} style={{ 
                                    display: "flex", 
                                    alignItems: "center", 
                                    gap: "4px",
                                    backgroundColor: "var(--bg-secondary)", 
                                    border: "1px solid var(--border-color)",
                                    borderRadius: "4px", 
                                    padding: "4px 8px", 
                                    fontSize: "0.75rem", 
                                    color: "var(--text-secondary)"
                                  }}>
                                    <FileText size={12} />
                                    <span style={{ maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                      {src}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{msg.content}</div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} style={{ height: "40px" }} />
            </div>
            {renderInputArea(false)}
          </>
        )}
      </div>
    </div>
  );
}
