import React, { useState, useEffect, useMemo } from 'react';
import {
    Users,
    CheckCircle,
    XCircle,
    RefreshCw,
    Settings,
    Plus,
    Trash2,
    ShieldCheck,
    Clock,
    Search,
    AlertTriangle,
    Key,
    FileCode,
    Globe,
    Terminal,
    ChevronRight,
    Database
} from 'lucide-react';

// 初始模拟数据扩展
const INITIAL_ACCOUNTS = [
    {
        id: 1,
        name: "Premium_User_01",
        status: "online",
        lastCheck: "2024-03-20 10:30",
        successRate: 98,
        method: "cookie",
        source: "Weibo",
        scriptType: "request",
        script: "fetch('api/login', { method: 'POST', body: JSON.stringify({cookie: val}) })"
    },
    {
        id: 2,
        name: "Crawler_Node_A",
        status: "online",
        lastCheck: "2024-03-20 10:28",
        successRate: 95,
        method: "credentials",
        source: "TikTok",
        scriptType: "browser",
        script: "await page.goto('login.html'); await page.type('#user', user); await page.click('#submit');"
    },
    {
        id: 3,
        name: "Test_Account_X",
        status: "expired",
        lastCheck: "2024-03-19 15:45",
        successRate: 42,
        method: "cookie",
        source: "Bilibili",
        scriptType: "request",
        script: "// Auto-refresh logic"
    },
];

const App = () => {
    const [accounts, setAccounts] = useState(INITIAL_ACCOUNTS);
    const [searchTerm, setSearchTerm] = useState("");
    const [updateFrequency, setUpdateFrequency] = useState(300);
    const [timeLeft, setTimeLeft] = useState(300);
    const [isUpdating, setIsUpdating] = useState(false);
    const [showAddModal, setShowAddModal] = useState(false);

    // 新账号表单状态
    const [newAcc, setNewAcc] = useState({
        name: "",
        source: "Google",
        method: "cookie", // 'cookie' | 'credentials'
        scriptType: "request", // 'request' | 'browser'
        content: "", // cookie content or password
        username: "",
        scriptCode: "// 输入您的自动化脚本逻辑..."
    });

    // --- 交互逻辑: 自动刷新 ---
    useEffect(() => {
        const timer = setInterval(() => {
            setTimeLeft((prev) => {
                if (prev <= 1) {
                    handleAutoRefresh();
                    return updateFrequency;
                }
                return prev - 1;
            });
        }, 1000);
        return () => clearInterval(timer);
    }, [updateFrequency]);

    const handleAutoRefresh = async () => {
        setIsUpdating(true);
        await new Promise(resolve => setTimeout(resolve, 2000));
        setAccounts(prev => prev.map(acc => ({
            ...acc,
            lastCheck: new Date().toLocaleString(),
            status: Math.random() > 0.15 ? "online" : "expired"
        })));
        setIsUpdating(false);
        setTimeLeft(updateFrequency);
    };

    const handleAddAccount = () => {
        const account = {
            id: Date.now(),
            name: newAcc.name || `User_${Math.floor(Math.random() * 1000)}`,
            status: "online",
            lastCheck: new Date().toLocaleString(),
            successRate: 100,
            method: newAcc.method,
            source: newAcc.source,
            scriptType: newAcc.scriptType,
            script: newAcc.scriptCode,
            cookie: newAcc.method === 'cookie' ? newAcc.content.substring(0, 15) + "..." : "Auto-Generated"
        };
        setAccounts([account, ...accounts]);
        setShowAddModal(false);
        // 重置表单
        setNewAcc({ name: "", source: "Google", method: "cookie", scriptType: "request", content: "", username: "", scriptCode: "// 输入您的自动化脚本逻辑..." });
    };

    const filteredAccounts = useMemo(() => {
        return accounts.filter(acc =>
            acc.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
            acc.source.toLowerCase().includes(searchTerm.toLowerCase())
        );
    }, [accounts, searchTerm]);

    const formatTime = (seconds) => {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${m}:${s < 10 ? '0' : ''}${s}`;
    };

    return (
        <div className= "min-h-screen bg-[#f8fafc] text-slate-900 font-sans" >
        {/* 顶部导航 */ }
        < header className = "bg-white border-b border-slate-200 px-8 py-4 sticky top-0 z-30 flex justify-between items-center shadow-sm" >
            <div className="flex items-center gap-3" >
                <div className="bg-indigo-600 p-2 rounded-xl shadow-indigo-200 shadow-lg" >
                    <Database className="text-white w-6 h-6" />
                        </div>
                        < div >
                        <h1 className="text-lg font-bold text-slate-800 leading-tight" > CookiePool Engine </h1>
                            < p className = "text-xs text-slate-500 font-medium uppercase tracking-wider" > Automated Session Manager </p>
                                </div>
                                </div>

                                < div className = "flex items-center gap-4" >
                                    <div className="flex items-center gap-3 bg-slate-100 px-4 py-2 rounded-xl border border-slate-200" >
                                        <Clock className="w-4 h-4 text-slate-500" />
                                            <span className="text-xs font-bold text-slate-600" > NEXT SYNC: { formatTime(timeLeft) } </span>
                                                </div>
                                                < button
    onClick = { handleAutoRefresh }
    disabled = { isUpdating }
    className = {`flex items-center gap-2 px-5 py-2 rounded-xl bg-indigo-600 text-white font-semibold text-sm hover:bg-indigo-700 transition-all ${isUpdating ? 'opacity-50 animate-pulse' : ''}`
}
          >
    <RefreshCw className={ `w-4 h-4 ${isUpdating ? 'animate-spin' : ''}` } />
{ isUpdating ? '正在同步脚本...' : '立即同步' }
</button>
    </div>
    </header>

    < main className = "max-w-7xl mx-auto p-8" >
        {/* 控制面板 */ }
        < div className = "flex flex-col md:flex-row gap-4 mb-8" >
            <div className="relative flex-1" >
                <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400 w-5 h-5" />
                    <input 
              type="text"
placeholder = "搜索账号标识、平台名称或登录脚本内容..."
className = "w-full pl-12 pr-4 py-3 bg-white border border-slate-200 rounded-2xl shadow-sm focus:ring-2 focus:ring-indigo-500 transition-all outline-none"
value = { searchTerm }
onChange = {(e) => setSearchTerm(e.target.value)}
            />
    </div>
    < div className = "flex gap-4" >
        <select 
              value={ updateFrequency }
onChange = {(e) => { setUpdateFrequency(Number(e.target.value)); setTimeLeft(Number(e.target.value)); }}
className = "px-4 py-3 bg-white border border-slate-200 rounded-2xl shadow-sm focus:ring-2 focus:ring-indigo-500 outline-none font-medium text-sm"
    >
    <option value={ 60 }> 1分钟检查 </option>
        < option value = { 300} > 5分钟检查 </option>
            < option value = { 3600} > 1小时检查 </option>
                </select>
                < button
onClick = {() => setShowAddModal(true)}
className = "flex items-center gap-2 px-6 py-3 bg-slate-900 text-white rounded-2xl font-bold hover:bg-slate-800 transition-all shadow-lg"
    >
    <Plus className="w-5 h-5" />
        接入新账号
        </button>
        </div>
        </div>

{/* 账号列表 */ }
<div className="bg-white rounded-3xl shadow-xl shadow-slate-200/50 border border-slate-100 overflow-hidden" >
    <table className="w-full text-left" >
        <thead>
        <tr className="bg-slate-50/50 border-b border-slate-100" >
            <th className="px-6 py-5 text-[11px] font-black text-slate-400 uppercase tracking-widest" > 账号信息 </th>
                < th className = "px-6 py-5 text-[11px] font-black text-slate-400 uppercase tracking-widest" > 登录模式 </th>
                    < th className = "px-6 py-5 text-[11px] font-black text-slate-400 uppercase tracking-widest" > 脚本引擎 </th>
                        < th className = "px-6 py-5 text-[11px] font-black text-slate-400 uppercase tracking-widest" > 运行状态 </th>
                            < th className = "px-6 py-5 text-[11px] font-black text-slate-400 uppercase tracking-widest" > 最近同步 </th>
                                < th className = "px-6 py-5 text-[11px] font-black text-slate-400 uppercase tracking-widest text-right" > 管理 </th>
                                    </tr>
                                    </thead>
                                    < tbody className = "divide-y divide-slate-50" >
                                        {
                                            filteredAccounts.map((acc) => (
                                                <tr key= { acc.id } className = "hover:bg-indigo-50/30 transition-colors group" >
                                                <td className="px-6 py-4" >
                                            <div className="flex items-center gap-3" >
                                            <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center font-bold text-slate-500 text-xs" >
                                            { acc.source[0] }
                                            </div>
                                            < div >
                                            <div className="font-bold text-slate-800 text-sm" > { acc.name } </div>
                                            < div className = "text-xs text-slate-400" > { acc.source } </div>
                                            </div>
                                            </div>
                                            </td>
                                            < td className = "px-6 py-4" >
                                            <div className="flex items-center gap-2" >
                                            {
                                                acc.method === 'cookie' ? (
                                                    <div className= "flex items-center gap-1.5 px-2.5 py-1 bg-blue-50 text-blue-600 rounded-lg text-[10px] font-black uppercase" >
                                                    <Terminal className="w-3 h-3" /> Cookie
                                                        </div>
                      ) : (
                                                            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-purple-50 text-purple-600 rounded-lg text-[10px] font-black uppercase" >
                                                        <Key className="w-3 h-3" /> Credentials
                                                        </div>
                                                        )}
</div>
    </td>
    < td className = "px-6 py-4" >
        <div className="flex flex-col gap-1" >
            <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-600" >
                { acc.scriptType === 'request' ? <Globe className="w-3.5 h-3.5 text-sky-500" /> : <Terminal className="w-3.5 h-3.5 text-amber-500" / >}
{ acc.scriptType === 'request' ? 'Request-based' : 'Browser-based' }
</div>
    < div className = "text-[10px] font-mono text-slate-400 truncate max-w-[150px]" >
        { acc.script }
        </div>
        </div>
        </td>
        < td className = "px-6 py-4" >
            <StatusBadge status={ acc.status } />
                </td>
                < td className = "px-6 py-4 text-xs font-medium text-slate-500" >
                    { acc.lastCheck }
                    </td>
                    < td className = "px-6 py-4 text-right" >
                        <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity" >
                            <button className="p-2 hover:bg-white rounded-lg border border-transparent hover:border-slate-200 transition-all text-slate-400 hover:text-indigo-600" >
                                <FileCode className="w-4 h-4" />
                                    </button>
                                    < button
onClick = {() => setAccounts(prev => prev.filter(p => p.id !== acc.id))}
className = "p-2 hover:bg-rose-50 rounded-lg text-slate-400 hover:text-rose-600 transition-all"
    >
    <Trash2 className="w-4 h-4" />
        </button>
        </div>
        </td>
        </tr>
              ))}
</tbody>
    </table>
    </div>
    </main>

{/* 弹窗: 添加账号 & 脚本配置 */ }
{
    showAddModal && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-md flex items-center justify-center z-50 p-6 overflow-y-auto" >
            <div className="bg-white rounded-[2rem] shadow-2xl max-w-2xl w-full border border-white overflow-hidden my-auto" >
                <div className="p-8 border-b border-slate-100 flex justify-between items-center bg-slate-50/50" >
                    <div>
                    <h3 className="text-xl font-black text-slate-800" > 接入新账号与脚本 </h3>
                        < p className = "text-sm text-slate-500 mt-1 font-medium" > 配置自动登录逻辑与身份验证信息 </p>
                            </div>
                            < button onClick = {() => setShowAddModal(false)
} className = "text-slate-400 hover:text-slate-600" >
    <XCircle className="w-6 h-6" />
        </button>
        </div>

        < div className = "p-8 space-y-6" >
            {/* 基本信息 */ }
            < div className = "grid grid-cols-2 gap-4" >
                <div className="space-y-2" >
                    <label className="text-xs font-bold text-slate-500 uppercase tracking-wider" > 账号标识名 </label>
                        < input
type = "text"
placeholder = "例如: Crawler_Node_01"
className = "w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none"
value = { newAcc.name }
onChange = {(e) => setNewAcc({ ...newAcc, name: e.target.value })}
                  />
    </div>
    < div className = "space-y-2" >
        <label className="text-xs font-bold text-slate-500 uppercase tracking-wider" > 所属平台 </label>
            < input
type = "text"
placeholder = "例如: Weibo, TikTok"
className = "w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none"
value = { newAcc.source }
onChange = {(e) => setNewAcc({ ...newAcc, source: e.target.value })}
                  />
    </div>
    </div>

{/* 登录方式切换 */ }
<div className="space-y-3" >
    <label className="text-xs font-bold text-slate-500 uppercase tracking-wider" > 验证模式 </label>
        < div className = "flex p-1 bg-slate-100 rounded-xl" >
            <button 
                    onClick={ () => setNewAcc({ ...newAcc, method: 'cookie' }) }
className = {`flex-1 py-2 rounded-lg text-sm font-bold transition-all ${newAcc.method === 'cookie' ? 'bg-white shadow-sm text-indigo-600' : 'text-slate-500'}`}
                  >
    Cookie 导入
        </button>
        < button
onClick = {() => setNewAcc({ ...newAcc, method: 'credentials' })}
className = {`flex-1 py-2 rounded-lg text-sm font-bold transition-all ${newAcc.method === 'credentials' ? 'bg-white shadow-sm text-indigo-600' : 'text-slate-500'}`}
                  >
    账号密码
    </button>
    </div>
    </div>

{/* 动态表单内容 */ }
{
    newAcc.method === 'cookie' ? (
        <div className= "space-y-2" >
        <label className="text-xs font-bold text-slate-500 uppercase tracking-wider" > Cookie 数据 </label>
            < textarea
    rows = "3"
    placeholder = "粘贴 JSON 或 Raw Cookie 字符串..."
    className = "w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none font-mono text-sm"
    value = { newAcc.content }
    onChange = {(e) => setNewAcc({ ...newAcc, content: e.target.value })
}
                  />
    </div>
              ) : (
    <div className= "grid grid-cols-2 gap-4" >
    <div className="space-y-2" >
        <label className="text-xs font-bold text-slate-500 uppercase tracking-wider" > 用户名 / 手机 </label>
            < input
type = "text"
className = "w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none"
value = { newAcc.username }
onChange = {(e) => setNewAcc({ ...newAcc, username: e.target.value })}
                    />
    </div>
    < div className = "space-y-2" >
        <label className="text-xs font-bold text-slate-500 uppercase tracking-wider" > 密码 </label>
            < input
type = "password"
className = "w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none"
value = { newAcc.content }
onChange = {(e) => setNewAcc({ ...newAcc, content: e.target.value })}
                    />
    </div>
    </div>
              )}

{/* 脚本配置部分 */ }
<div className="p-5 bg-slate-900 rounded-2xl space-y-4" >
    <div className="flex justify-between items-center" >
        <div className="flex items-center gap-2 text-white font-bold text-sm" >
            <Terminal className="w-4 h-4 text-indigo-400" />
                自定义登录脚本(Login Script)
                </div>
                < select
value = { newAcc.scriptType }
onChange = {(e) => setNewAcc({ ...newAcc, scriptType: e.target.value })}
className = "bg-slate-800 text-slate-300 text-[10px] font-black uppercase px-2 py-1 rounded border border-slate-700 outline-none"
    >
    <option value="request" > Request Engine </option>
        < option value = "browser" > Browser Engine </option>
            </select>
            </div>
            < div className = "text-[10px] text-slate-400 font-medium" >
            {
                newAcc.scriptType === 'request'
                    ? "使用内置 Request 库模拟 HTTP 协议登录，性能极高，资源占用低。"
                    : "启动 Headless 浏览器执行 DOM 操作登录，适用于复杂滑块和加密逻辑。"
            }
                </div>
                < textarea
rows = "4"
className = "w-full bg-slate-800 border-none rounded-xl p-4 text-indigo-300 font-mono text-xs focus:ring-1 focus:ring-indigo-500 outline-none"
value = { newAcc.scriptCode }
onChange = {(e) => setNewAcc({ ...newAcc, scriptCode: e.target.value })}
                />
    </div>

    < div className = "flex gap-4 pt-4" >
        <button onClick={ () => setShowAddModal(false) } className = "flex-1 py-4 text-slate-500 font-bold hover:bg-slate-50 rounded-2xl transition-all" >
            取消
            </button>
            < button
onClick = { handleAddAccount }
className = "flex-1 py-4 bg-indigo-600 text-white font-bold rounded-2xl hover:bg-indigo-700 hover:shadow-xl hover:shadow-indigo-200 transition-all flex items-center justify-center gap-2"
    >
    <ShieldCheck className="w-5 h-5" />
        保存并启动脚本
        </button>
        </div>
        </div>
        </div>
        </div>
      )}
</div>
  );
};

// --- 组件部分 ---

const StatusBadge = ({ status }) => {
    const configs = {
        online: { label: "RUNNING", class: "bg-emerald-50 text-emerald-600 border-emerald-100", dot: "bg-emerald-500" },
        warning: { label: "LIMITED", class: "bg-amber-50 text-amber-600 border-amber-100", dot: "bg-amber-500" },
        expired: { label: "FAILED", class: "bg-rose-50 text-rose-600 border-rose-100", dot: "bg-rose-500" },
    };
    const config = configs[status];
    return (
        <span className= {`flex items-center gap-2 px-3 py-1 rounded-lg text-[10px] font-black border ${config.class}`
}>
    <span className={ `w-1.5 h-1.5 rounded-full ${config.dot} animate-pulse` } />
{ config.label }
</span>
  );
};

export default App;