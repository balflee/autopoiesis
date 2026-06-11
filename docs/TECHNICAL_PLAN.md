# Genesis Experiment — Technical Implementation Plan (v0.1)

> 配套文档：[PRD.md](./PRD.md)
> 状态：待评审 / 待回答 3 个开放问题（见末尾）

---

## 0. 设计原则

1. **PRD 即真理**：所有技术决策服务于「三阶段生命周期 + Permadeath + Demo 戏剧性」
2. **窄而深**：能砍的全砍。Stylus 砍、LangGraph 砍、多 Agent 砍、复杂 RL 算法砍
3. **链上必要性优先**：写进合约的必须是「离开链就不成立」的逻辑（生死、能量、Tombstone）
4. **Demo 永远赢**：任何技术选择如果让 Demo 视频更难拍，就不选
5. **Phase 2 deadline 不可动摇**：Week 2 末必须上线

---

## 1. 系统拓扑

```
┌──────────────────────────────────────────────────────────────────────┐
│                            外部世界                                   │
├──────────────────────────────────────────────────────────────────────┤
│  balldontlie.io   Polymarket    Reddit         Twitter      Claude   │
│   (NBA 数据)       (盘口)       (社交)         (社交)        (LLM)    │
└──────┬───────────────┬──────────────┬─────────────┬───────────┬─────┘
       │               │              │             │           │
       └───────────────┴──────────────┴─────────────┴───────────┘
                                  │
                ┌─────────────────▼──────────────────┐
                │         Agent Backend (Python)      │
                │  ┌──────────────────────────────┐   │
                │  │  Data Ingestion              │   │
                │  │  Technical Engine            │   │
                │  │  Sentiment Engine (Phase2+)  │   │
                │  │  Decision Engine             │   │
                │  │  Reflection Engine (LLM)     │   │
                │  │  Weight Updater (P1/P2)      │   │
                │  │  Chain Adapter (web3.py)     │   │
                │  └──────────┬──────────────┬────┘   │
                └─────────────┼──────────────┼────────┘
                              │              │
                              │ tx           │ WebSocket
                              ▼              ▼
                ┌──────────────────────────────────┐
                │   Arbitrum Orbit L3 (custom Gas) │
                │   ┌──────────────────────────┐   │
                │   │ EnergyController         │   │
                │   │ PhaseManager             │   │
                │   │ AgentLifecycle           │   │
                │   │ DecisionLog              │   │
                │   │ TombstoneNFT (ERC-721)   │   │
                │   └──────────────────────────┘   │
                └──────────┬──────────────────┬────┘
                           │ events           │
                           ▼                  │
                ┌──────────────────────────┐  │
                │  Dashboard (Next.js)     │  │
                │  - Vitals Panel          │◄─┘
                │  - Consciousness Stream  │
                │  - Dual Engine Meter     │
                │  - Evolution Curve       │
                │  - Death Watch (终幕)    │
                └──────────────────────────┘
```

---

## 2. 技术栈（终选）

| 层 | 选型 | 理由 |
|---|---|---|
| **L3 链** | Arbitrum Orbit（RaaS 模板） | 自定义 Gas 模型是 Permadeath 经济的基础 |
| **智能合约** | Solidity 0.8.x + Hardhat / Foundry | 标准选项，部署快 |
| **Agent Backend** | Python 3.11 + asyncio | 简单、控制力强，AI 库齐全 |
| **链交互** | web3.py（Python 侧） + viem（前端侧） | Python 后端 + TS 前端，各用各的成熟方案 |
| **Polygon RPC** | Alchemy 或 QuickNode 免费 tier | Smart Money 钱包识别 + 实时仓位订阅 |
| **LLM** | Anthropic Claude API（Sonnet 4.6 默认，Opus 用于关键决策） | 缓存友好，质量高 |
| **数据库** | PostgreSQL（Agent 状态、训练数据） | 标准，备份方便 |
| **训练框架** | scikit-learn + NumPy（无 PyTorch） | 权重只有 3 个，杀鸡不需要牛刀 |
| **Dashboard** | Next.js 14 (App Router) + Tailwind + shadcn/ui + viem | 模板化高效 |
| **实时通讯** | WebSocket（Agent → Dashboard） + 链上 event 订阅 | 双通道：进程内事件 + 链上权威事件 |
| **部署** | Backend: Railway / Fly.io；Frontend: Vercel | 不浪费时间在 infra 上 |
| **故意不选** | LangGraph、LangChain、PyTorch、Stylus、Docker Compose 复杂编排 | 避免学习成本和绑手绑脚 |

---

## 3. 智能合约设计

共 5 个核心合约 + 1 个可选合约。所有合约用 Solidity 0.8.x，OpenZeppelin 库。

### 3.1 `EnergyController`（生存机制核心，v3.1）

**职责**：管理 Agent 的 BREATH 生命余额、bankroll mirror、所有阶段切换触发、所有反 exploit 检查。

```solidity
import {EIP712}            from "@oz/utils/cryptography/EIP712.sol";
import {ECDSA}             from "@oz/utils/cryptography/ECDSA.sol";
import {ReentrancyGuard}   from "@oz/utils/ReentrancyGuard.sol";
import {Pausable}          from "@oz/utils/Pausable.sol";

contract EnergyController is EIP712, ReentrancyGuard, Pausable {
    // === 当前余额（唯一权威，死亡判定依据）===
    uint256 public breath;                           // BREATH 当前余额（1e6 精度）
    uint256 public initialBreath;                    // 创世时注入量
    uint256 public maxBreath;                        // 软上限（可由 deepenBreath 提升）

    // === 历史归因（仅 dashboard / 校准用，不参与 burn 算术）===
    uint256 public cumulativeEarnedBreath;
    uint256 public cumulativeDonatedBreath;
    uint256 public cumulativeBurnedBreath;

    // === Polymarket bankroll 镜像（来自 Polygon oracle）===
    uint256 public bankrollUsdcMirror;
    uint256 public latestBankrollNonce;

    // === 时间追踪 ===
    uint256 public lastBurnTimestamp;
    uint256 public lastBetTimestamp;                 // 仅 BET 重置（idle decay 用）
    uint256 public terminalEnteredAt;                // Terminal 进入时刻

    // === 阶段状态 ===
    Phase   public currentPhase;
    bool    public desperateMode;
    bool    public terminalLucidity;
    bool    public starvationMode;
    uint8   public pressureSustainedCycles;
    uint8   public deepenCount;
    uint16  public episodeNumber;                    // Apprenticeship Failure 计数

    // === Bet timestamp 验证（Terminal 后规则）===
    mapping(bytes32 => bool)    public knownBetIds;
    mapping(bytes32 => uint256) public betPlacedAt;
    mapping(bytes32 => bool)    public settledBetIds;   // replay protection

    // === Donation 节流 + 归因 ===
    mapping(uint256 => uint256) public donatedInHour;  // hour timestamp → 累计
    uint256 public constant DONATION_HOURLY_CAP = 500e6;
    uint256 public constant DONATION_RATE       = 1000e6;  // 占位：1 ETH = 1000 BREATH

    // === Attestation signer ===
    address public attestationSigner;
    uint256 public constant ATTESTATION_MAX_AGE = 10 minutes;

    // === 死亡追踪 ===
    enum TransitionReason { NONE, PASSIVE_BURN, ACTION_COST, SETTLEMENT_WIN, SETTLEMENT_LOSS, DONATION, DEEPEN }
    TransitionReason public lastTransitionReason;

    // === EIP-712 type hashes ===
    bytes32 private constant BET_SETTLEMENT_TYPEHASH = keccak256(
        "BetSettlement(bytes32 betId,bytes32 marketId,int256 pnlUsdE6,uint256 stakeUsd,uint256 payoutUsd,uint256 bankrollAfter,uint256 timestamp,address agentAddress)"
    );
    bytes32 private constant BANKROLL_UPDATE_TYPEHASH = keccak256(
        "BankrollUpdate(uint256 newBankroll,uint256 timestamp,uint256 nonce)"
    );

    // === Errors（Gas-friendly） ===
    error AlreadySettled();
    error BadSignature();
    error StaleAttestation();
    error OldNonce();
    error UnknownBet();
    error PostTerminalBet();
    error TerminalNoDonations();
    error HourlyCapReached();
    error InsufficientBreath();
    error NotInPhase(Phase required);
    error CannotDeepenNow();
    error AgentDead();
    error BetIdReused();
    error UseBetDecisionFn();

    // === Burn 函数族 ===
    function applyPassiveBurn() external nonReentrant {
        _applyBurnSinceLast();
        _checkAllStateTransitions();
    }

    function consumeAction(ActionType action) external onlyAgent nonReentrant {
        if (breath == 0) revert AgentDead();
        if (action == ActionType.BET) revert UseBetDecisionFn();   // BET 走原子化函数
        _applyBurnSinceLast();
        uint256 cost = _actionCost(action);
        _burn(cost, TransitionReason.ACTION_COST);
        _checkAllStateTransitions();
    }

    /// @notice BET 决策的原子化入口：记录 betId + 扣 BET cost + 重置 idle 计时
    /// @dev 替代旧的 recordBetPlaced() + consumeAction(BET) 分离调用，
    ///      确保 betId 与 BET 扣血一致，避免 partial state（扣了血没记 id 或反之）。
    function recordBetDecisionAndConsume(
        bytes32 betId,
        bytes32 marketId,
        uint256 intendedSizeUsd
    ) external onlyAgent nonReentrant {
        if (breath == 0)         revert AgentDead();
        if (knownBetIds[betId])  revert BetIdReused();

        _applyBurnSinceLast();
        _burn(_actionCost(ActionType.BET), TransitionReason.ACTION_COST);

        knownBetIds[betId]  = true;
        betPlacedAt[betId]  = block.timestamp;
        lastBetTimestamp    = block.timestamp;   // 仅 BET 重置 idle decay 计时

        emit BetDecisionRecorded(betId, marketId, intendedSizeUsd, block.timestamp);
        _checkAllStateTransitions();
    }

    // === Settlement 主路径（含 EIP-712 + replay 防护 + Terminal 检查）===
    /// @dev replay protection: settledBetIds[betId] (betId 本身即唯一 replay key)
    ///      没有独立 nonce field —— betId 一次性使用，不需要额外计数器
    function settleBet(
        bytes32 betId,
        bytes32 marketId,
        int256  pnlUsdE6,
        uint256 stakeUsd,
        uint256 payoutUsd,
        uint256 bankrollAfter,
        uint256 timestamp,
        bytes calldata sig
    ) external nonReentrant {
        if (settledBetIds[betId])                            revert AlreadySettled();
        if (block.timestamp - timestamp > ATTESTATION_MAX_AGE) revert StaleAttestation();

        bytes32 structHash = keccak256(abi.encode(
            BET_SETTLEMENT_TYPEHASH,
            betId, marketId, pnlUsdE6, stakeUsd, payoutUsd,
            bankrollAfter, timestamp, agentAddress()
        ));
        bytes32 digest = _hashTypedDataV4(structHash);
        if (ECDSA.recover(digest, sig) != attestationSigner) revert BadSignature();

        if (terminalLucidity) {
            if (!knownBetIds[betId])                         revert UnknownBet();
            if (betPlacedAt[betId] >= terminalEnteredAt)     revert PostTerminalBet();
        }

        settledBetIds[betId] = true;

        // 对称扣加
        if (pnlUsdE6 > 0) {
            uint256 gain = uint256(pnlUsdE6) * CONVERSION_RATE;
            _mintBreath(gain, TransitionReason.SETTLEMENT_WIN);
            cumulativeEarnedBreath += gain;
        } else if (pnlUsdE6 < 0) {
            uint256 loss = uint256(-pnlUsdE6) * CONVERSION_RATE;
            _burn(loss, TransitionReason.SETTLEMENT_LOSS);
        }

        // 更新 bankroll 镜像（settlement 携带最新值）
        bankrollUsdcMirror = bankrollAfter;

        _checkAllStateTransitions();
    }

    // === Donation: External Life Support（含 hourly cap）===
    function donate() external payable nonReentrant whenNotPaused {
        if (terminalLucidity)               revert TerminalNoDonations();
        if (currentPhase != Phase.Adulthood) revert NotInPhase(Phase.Adulthood);

        uint256 donationBreath = msg.value * DONATION_RATE / 1e18;
        uint256 hr = block.timestamp / 1 hours;
        uint256 newTotal = donatedInHour[hr] + donationBreath;
        if (newTotal > DONATION_HOURLY_CAP) revert HourlyCapReached();
        donatedInHour[hr] = newTotal;

        breath += donationBreath;                        // ← 加本次量
        cumulativeDonatedBreath += donationBreath;
        lastTransitionReason = TransitionReason.DONATION;

        emit DonationReceived(donationBreath, msg.sender);
    }

    // === Bankroll mirror 更新（含 replay 防护）===
    function updateBankrollMirror(
        uint256 newBankroll,
        uint256 timestamp,
        uint256 nonce,
        bytes calldata sig
    ) external {
        if (nonce <= latestBankrollNonce)                   revert OldNonce();
        if (block.timestamp - timestamp > ATTESTATION_MAX_AGE) revert StaleAttestation();

        bytes32 structHash = keccak256(abi.encode(
            BANKROLL_UPDATE_TYPEHASH, newBankroll, timestamp, nonce
        ));
        bytes32 digest = _hashTypedDataV4(structHash);
        if (ECDSA.recover(digest, sig) != attestationSigner) revert BadSignature();

        latestBankrollNonce  = nonce;
        bankrollUsdcMirror   = newBankroll;
        _checkStarvation();
    }

    // === Lung Expansion ===
    function deepenBreath() external onlyAgent nonReentrant {
        if (!_canDeepen()) revert CannotDeepenNow();
        uint256 cost = nextDeepenCost();
        _burn(cost, TransitionReason.DEEPEN);
        deepenCount++;
        maxBreath += 1000e6;
        emit LungExpanded(deepenCount, maxBreath, cost);
    }

    // === Views ===
    function totalBreath() external view returns (uint256) { return breath; }
    function projectedSurvivalHours() external view returns (uint256);
    function currentPressure() external view returns (uint256);
    function effectiveBurnRate() external view returns (uint256);
    function nextDeepenCost() public view returns (uint256) {
        // 2000 × 1.5^deepenCount
        return _exp(2000e6, deepenCount, 15, 10);  // 1.5 ratio as 15/10
    }

    // === Internal: 状态机检查 ===
    function _checkAllStateTransitions() internal {
        if (breath == 0) {
            _emitAndDie();
            return;
        }
        if (breath <= initialBreath * 5 / 100 && !terminalLucidity) {
            terminalLucidity   = true;
            terminalEnteredAt  = block.timestamp;
            emit TerminalLucidityEntered(block.number);
        }
        _checkPressureAndDesperate();
        _checkStarvation();
    }

    function _checkPressureAndDesperate() internal {
        if (desperateMode || currentPhase != Phase.Adulthood) return;
        uint256 p = currentPressure();
        if (p >= 0.5e6) {
            pressureSustainedCycles++;
            if (pressureSustainedCycles >= 2) {
                desperateMode = true;
                emit DesperateModeEntered(block.number);
            }
        } else {
            pressureSustainedCycles = 0;   // 滞回 reset
        }
    }

    function _checkStarvation() internal {
        bool shouldStarve = bankrollUsdcMirror < MIN_BET_SIZE;
        if (shouldStarve && !starvationMode) {
            starvationMode = true;
            emit StarvationBegan(block.number);
        } else if (!shouldStarve && starvationMode) {
            starvationMode = false;
            emit StarvationEnded(block.number);
        }
    }

    function _emitAndDie() internal {
        DeathCause cause = _determineDeathCause();
        bool afterglow = terminalLucidity;
        uint256 tombstoneId = TombstoneNFT(tombstoneAddr).mint(
            address(this), cause, afterglow,
            breath, cumulativeEarnedBreath, cumulativeDonatedBreath
        );
        emit Death(tombstoneId, cause, afterglow);
        AgentLifecycle(lifecycleAddr).die();
    }

    function _determineDeathCause() internal view returns (DeathCause) {
        // 优先级：TradingLoss > Starvation > Attrition
        if (lastTransitionReason == TransitionReason.SETTLEMENT_LOSS) return DeathCause.TradingLoss;
        if (starvationMode)                                            return DeathCause.Starvation;
        return DeathCause.Attrition;
    }

    // === Events ===
    event BreathBurned(uint256 amount, TransitionReason reason);
    event BreathReplenished(uint256 amount, TransitionReason reason, bytes32 indexed betId);
    event BetDecisionRecorded(bytes32 indexed betId, bytes32 indexed marketId, uint256 intendedSizeUsd, uint256 timestamp);
    event DonationReceived(uint256 amount, address indexed donor);
    event OverflowBurned(uint256 amount);
    event DesperateModeEntered(uint256 blockNum);
    event TerminalLucidityEntered(uint256 blockNum);
    event StarvationBegan(uint256 blockNum);
    event StarvationEnded(uint256 blockNum);
    event LungExpanded(uint8 newCount, uint256 newMaxBreath, uint256 costPaid);
    event LastWordsRecorded(bytes32 textHash, string short280, string ipfsCid);
    event ApprenticeshipFailed(uint16 episodeNumber, uint256 blockNum);
    event Death(uint256 indexed tombstoneTokenId, DeathCause cause, bool terminalAfterglow);
}

enum Phase       { Childhood, Apprenticeship, Adulthood, Dead }
enum DeathCause  { TradingLoss, Starvation, Attrition }
enum ActionType  { BET, NO_BET, REFLECT, THOUGHT, WEIGHT_COMMIT, LAST_WORDS, DEEPEN }
```

**设计要点**：
- **单一 `breath` 余额**：cumulativeEarned/Donated 仅用于 dashboard 归因，不参与扣血算术（避免 underflow）
- **EIP-712 签名**：BetSettlement 和 BankrollUpdate 都用 typed data，含 chainId / verifyingContract 防跨域 replay
- **replay 保护**：`settledBetIds` mapping + `latestBankrollNonce` 单调递增
- **Terminal pre-bet 检查**：`knownBetIds + betPlacedAt < terminalEnteredAt`
- **Pressure 滞回**：2 个连续 cycle 才触发 Desperate，避免瞬时抖动
- **Starvation 仅 bankroll 决定**：donate 解不开
- **Death Cause 优先级**：TradingLoss > Starvation > Attrition（写死）
- **Donation 分账 + hourly cap**：评委续命受限
- **Pausable 仅在 Phase 1/2 可用**：Phase 3 `transitionToAdulthood()` 时合约**自动 renounce pause + upgrade role**（转 burn 地址，不可逆），保障 Permadeath trustless（项目方也无法救它）
- **ReentrancyGuard / Custom errors / Foundry fuzz** 是标准 hygiene

详见 §3.7 EIP-712 schemas 和 §6 Survival Mechanism 全文。

### 3.2 `PhaseManager`
**职责**：维护 Phase 1/2/3 状态机，记录权重 hash。

```solidity
contract PhaseManager {
    enum Phase { Childhood, Apprenticeship, Adulthood, Dead }
    Phase public currentPhase;
    bytes32 public currentWeightsHash;    // 当前权重的 keccak256
    bool    public weightsFrozen;         // Phase 3 启动后为 true
    bool    public desperateMode;         // 由 EnergyController 在 pressure≥0.5 持续 2 cycles 后触发

    function transitionTo(Phase next, bytes32 weightsHash) external onlyAdmin;
    function updateWeights(bytes32 newHash) external onlyAgent;
    // 在 Phase 3 且非 desperate 时 revert；desperate 模式下仅允许更新 β₁/β₂/ρ 的子哈希
    function enterDesperateMode() external onlyEnergyController;
    function getPhase() external view returns (Phase);

    event PhaseTransitioned(Phase from, Phase to, bytes32 weightsHash, uint256 timestamp);
    event WeightsFrozen(bytes32 finalHash);
    event DesperateModeEntered(uint256 blockNum, uint256 energyAtTrigger);
}
```

**设计要点**：
- 状态机单向：Childhood → Apprenticeship → Adulthood → Dead
- Phase 3 启动时自动冻结权重，之后再调 `updateWeights` 会 revert
- **绝境觉醒**：`EnergyController` 在 `pressure ≥ 0.5` **持续 2 个 decision cycles**（约 90 分钟，含滞回防抖）后调用 `enterDesperateMode()`。此后 `updateWeights` 在「仅 β₁/β₂/ρ 子集」范围内允许（链上仅校验权重 hash，子集合法性由 off-chain Agent 保证 + 复盘时可验证）
- 权重本身存链下，链上只存 hash 作为 commitment

### 3.3 `AgentLifecycle`
**职责**：生死状态机，触发临终遗言和 Tombstone NFT mint。

```solidity
contract AgentLifecycle {
    bool    public isAlive;
    bool    public terminalLucidityMode;     // breath ≤ 5% × INITIAL_BREATH 时进入（sticky）
    uint256 public deathBlock;

    // Last Words 存储（与 EnergyController v3.1 统一）
    bytes32 public lastWordsHash;            // keccak256 of full text（不可篡改证明）
    string  public shortLastWords;           // ≤ 280 字符的精华版（on-chain 直读）
    string  public lastWordsCid;             // 可选：IPFS CID 指向 full reflection

    function enterTerminalLucidity() external onlyEnergyController;
    function setLastWords(
        bytes32 textHash,
        string calldata short280,
        string calldata ipfsCid
    ) external onlyAgent;                    // 仅 Terminal 后一次性可调
    function die() external onlyEnergyController;  // 触发 Tombstone mint

    event TerminalLucidityActivated(uint256 block);
    event LastWordsRecorded(bytes32 textHash, string short280, string ipfsCid);
    event AgentDied(uint256 deathBlock, uint256 tombstoneTokenId, DeathCause cause, bool terminalAfterglow);
}
```

**设计要点**：
- `terminalLucidityMode` 一旦置 true 永不重置（sticky state）
- `setLastWords` 仅在 terminalLucidityMode 后可调，且只能调一次
- `die()` 由 EnergyController 触发，调用前 EnergyController 已确定 deathCause（写入 lastTransitionReason）
- AgentDied event 携带 (cause, terminalAfterglow) 用于 Tombstone NFT metadata 和分析

### 3.4 `DecisionLog`
**职责**：把 Agent 的每一次下注决策、复盘、思维流上链。

```solidity
contract DecisionLog {
    struct Decision {
        uint256 timestamp;
        bytes32 marketId;          // Polymarket market 标识
        uint8   choice;            // 0 = side A, 1 = side B
        uint256 size;              // 下注规模
        int256  technicalScore;    // 理性引擎分数（定点数）
        int256  sentimentScore;    // 感性引擎分数
        bytes32 reasoningHash;     // 详细思维流的 IPFS / 字符串 hash
    }

    mapping(uint256 => Decision) public decisions;
    uint256 public decisionCount;

    function logDecision(Decision memory d) external onlyAgent;
    function logReflection(uint256 decisionId, bytes32 reflectionHash) external onlyAgent;

    event DecisionLogged(uint256 indexed id, bytes32 marketId, uint256 timestamp);
    event ReflectionLogged(uint256 indexed decisionId, bytes32 reflectionHash);
}
```

**设计要点**：
- 关键字段上链，长文本（reasoning, reflection）存 IPFS 或链下，只上 hash
- 这样既有可验证性，又控制 Gas

### 3.5 `TombstoneNFT` (ERC-721)
**职责**：死亡时自动 mint，承载 Agent 的全部遗产。

```solidity
contract TombstoneNFT is ERC721 {
    struct Tombstone {
        bytes32 finalWeightsHash;
        string  lastWords;
        uint256 birthBlock;
        uint256 deathBlock;
        uint256 totalDecisions;
        uint256 finalWinRate;       // ×10000，定点数
        bytes32 lineageOf;          // 上一代 token id（V2 用），首代为 0
        string  memoryBankCid;      // IPFS CIDv1 of complete memory_bank tarball;
                                    //   empty string if IPFS pin failed at mint
                                    //   (degraded mode — TombstoneMintedWithoutMemoryBank
                                    //   event emitted; mint still succeeds).
                                    //   See PRD §5.1 C and §13 V2 lineage.
    }

    event TombstoneMintedWithoutMemoryBank(
        uint256 indexed tokenId,
        string reason                          // "ipfs_pin_failed_after_3_retries"
    );

    function mintTombstone(
        bytes32 finalWeightsHash_,
        string calldata lastWords_,
        uint256 totalDecisions_,
        uint256 finalWinRate_,
        bytes32 lineageOf_,
        string calldata memoryBankCid_         // may be "" — degraded mode
    ) external onlyLifecycle returns (uint256);

    function tokenURI(uint256 tokenId) public view override returns (string memory);
    // tokenURI 返回 on-chain 生成的 SVG + metadata（生死曲线视觉化）+
    //   external "ipfs://<memoryBankCid>" link for full mind browsing if CID non-empty
}
```

**设计要点**：
- 一旦 mint，永远不可修改
- `tokenURI` 用 on-chain SVG 渲染（不依赖外部 hosting）；memoryBankCid 作为可选外链
- 这是项目最有收藏价值的产物 —— 不只是 JPG + 几个 hash，而是一个**真实存在过的 AI 心智的数字遗骸**（NFT 持有者可逐 tick 浏览 Agent 一生决策）
- **Degraded-mode 失败语义**：IPFS pin 失败（Pinata 503 / gateway 故障）走重试 3 次后 mint 仍然成功，`memoryBankCid=""`，事件警报。避免「IPFS 暂时不可用」阻塞 `kill()` 调用从而卡死 Agent 死亡流程。Tombstone 仍然 mint；仅缺少 mind-browsing 入口

### 3.6 `ExternalBettingMarket`（**V2 / Stretch — 已延后**）
不在 Week 1/2 工作量内。Week 2 末若进度提前才考虑最小版本。

### 3.7 EIP-712 Attestation Schemas

所有 self-oracle attestation 走 EIP-712 typed data 签名，避免 raw bytes signature 的 replay / domain confusion 风险。

#### Domain Separator

```solidity
EIP712Domain({
    name:              "GenesisExperiment",
    version:           "1",
    chainId:           <Orbit L3 chain id>,
    verifyingContract: <EnergyController address>
})
```

#### Type: BetSettlement

```solidity
struct BetSettlement {
    bytes32 betId;            // 唯一标识（用作 replay key —— settledBetIds[betId] 一次性）
    bytes32 marketId;         // Polymarket market id
    int256  pnlUsdE6;         // 盈亏（USD × 1e6），有符号
    uint256 stakeUsd;         // 本金
    uint256 payoutUsd;        // 派彩
    uint256 bankrollAfter;    // 结算后 bankroll 余额（用于更新 mirror）
    uint256 timestamp;        // attestation 生成时刻（≤ 10 min stale）
    address agentAddress;     // Agent 自己的地址（防跨账户 replay）
}
// 注：betId 自身即为 replay 键，不需要独立 nonce 字段
```

#### Type: BankrollUpdate

```solidity
struct BankrollUpdate {
    uint256 newBankroll;
    uint256 timestamp;
    uint256 nonce;            // 单调递增（latestBankrollNonce 校验）
}
```

#### 签名流程（Python 侧）

```python
# Agent 后端持有 attestation private key
from eth_account.messages import encode_typed_data

attestation = {
    "types": {
        "BetSettlement": [
            {"name": "betId",         "type": "bytes32"},
            {"name": "marketId",      "type": "bytes32"},
            ...
        ]
    },
    "primaryType": "BetSettlement",
    "domain": {
        "name": "GenesisExperiment",
        "version": "1",
        "chainId": orbit_chain_id,
        "verifyingContract": energy_controller_addr,
    },
    "message": {
        "betId": settlement.bet_id,
        "marketId": settlement.market_id,
        "pnlUsdE6": settlement.pnl_usd * 1_000_000,
        ...
    }
}

signed = account.sign_typed_data(full_message=attestation)
tx     = energy_controller.functions.settleBet(..., signed.signature).transact()
```

#### Security 边界（V1 demo）
- Agent 持有 attestation key（V1 信任假设）
- V2 升级路径：用 LayerZero / Hyperlane 真桥替代 self-oracle，去除信任假设
- 在 Demo 中诚实说明：「This is a V1 trust-minimized model. V2 uses native cross-chain messaging.」

---

## 4. Agent Backend 模块设计

文件结构：

```
agent/
├── core/
│   ├── agent.py              # 主循环（Phase 2/3 复用）
│   ├── state.py              # 内存状态 + 权重
│   ├── lifecycle.py          # Phase 切换、生死判断
│   ├── memory_bank.py        # 每 tick 结构化持久化：write_tick / load_last_k_ticks /
│   │                         #   write_postmortem。Atomic temp+rename 写入 5 个文件
│   │                         #   (signals/fusion/decision/weights/outcome) per tick
│   │                         #   under .agent_state/memory_bank/{ticks,summary,...}/
│   │                         #   See PRD §4.6 + §8 中部 PLAYBACK 模式。
│   ├── memory_bank_schema.json   # 共享 JSON Schema (agent 写 + sim 读 + dashboard 读 +
│   │                         #   V2 boot loader 读)。Per-tick records carry
│   │                         #   `schema_version` for forward/backward compat.
│   ├── memory_bank_migrations.py # MIGRATIONS = {("1.0","1.1"): _migrate_1_0_to_1_1, ...}
│   │                         #   Reader-side migration chain; agent boots may
│   │                         #   straddle schema bumps (live agents in prod).
│   ├── narrative.py          # Per-tick 1-2 句 narrative writer (LLM Haiku tier).
│   │                         #   Fail-fast → deterministic template on LLM error.
│   │                         #   Writes to memory_bank/summary/tick_<N>_brief.md.
│   │                         #   See PRD §4.6.
│   └── v2_boot.py            # V2 lineage boot loader stub (post-hackathon).
│                             #   Reads ancestor's Tombstone NFT memoryBankCid →
│                             #   pulls tarball from IPFS → injects last K=50 ticks
│                             #   into new agent's reflection context. See PRD §13.
├── engines/
│   ├── nba_technical.py      # α₁：NBA 统计特征 → 胜率预测
│   ├── market_momentum.py    # α₂：Polymarket 盘口动量
│   ├── smart_money.py        # α₃：Polygon 链上 top wallet 跟踪
│   ├── sentiment_llm.py      # β₁：Phase 2/3 用 LLM 情绪
│   ├── crowd_volume.py       # β₂：Reddit 数值代理（关注度）
│   ├── decision.py           # 2 层融合 + Kelly 下注
│   ├── reflection.py         # LLM 复盘日志
│   └── weight_updater.py     # 权重学习（P1/P2 only，2 层）
├── data/
│   ├── nba_historical.py     # balldontlie 历史拉取
│   ├── nba_live.py           # 实时比赛状态
│   ├── reddit_historical.py  # Pushshift dump 处理
│   ├── reddit_live.py        # PRAW 实时
│   ├── twitter_live.py       # Apify scraper
│   ├── polymarket.py         # 盘口（历史 + 实时，含 orderbook 深度）
│   └── polygon_chain.py      # Polygon 链上 Polymarket 合约事件 ETL
├── chain/
│   ├── client.py             # web3.py 包装
│   ├── contracts.py          # ABI loader
│   └── tx_manager.py         # nonce 管理、Gas 估算、retry
├── training/
│   ├── phase1_runner.py      # 历史训练入口
│   └── feature_engineering.py
├── dashboard_bridge/
│   ├── websocket_server.py   # 向 Dashboard 推流
│   └── event_emitter.py
└── main.py                   # entry point

sim/                          # Track C：Layer 2 校准模拟（Day 1–4）
├── economy.py                # 完整 BREATH 经济引擎（v3.1 spec）
├── strategies.py             # 3 个 archetype：Pessimist / Optimist / Satisficer
├── market.py                 # 历史 NBA + Polymarket 数据回放
├── runner.py                 # 单次 lifetime 模拟
├── sweeper.py                # Latin Hypercube + Bayesian Optimization
├── objectives.py             # GOOD_CALIBRATION 判定逻辑
├── analysis.py               # 输出统计 + 可视化
├── replay.py                 # NEW: 加载 memory_bank tarball → sim 的 trajectory 格式。
│                             #   让 sim 可以「回放真 Agent 的一生」作为一个 sim
│                             #   trajectory，与 1000 个 Monte Carlo 虚拟一生对比。
│                             #   Schema 共享自 agent/core/memory_bank_schema.json
│                             #   (memory_bank_schema 是 agent + sim + dashboard +
│                             #   V2 boot loader 的共同契约)。Demo asset：可视化
│                             #   「真 Agent vs 1000 个未走的虚拟人生」的分歧点。
└── notebooks/
    └── calibration.ipynb     # 校准报告生成
```

### 4.1 主循环（Phase 2/3）

```python
async def agent_loop():
    while alive:
        # 1. 检查能量，可能进入临终模式
        energy = await chain.get_energy_or_die()
        if energy < THRESHOLD_TERMINAL:
            await enter_terminal_lucidity()

        # 2. 找下一个 actionable market
        upcoming = await polymarket.fetch_upcoming_nba_markets()
        target = pick_next_market(upcoming)

        # 3. 5 个子信号并行打分（asyncio.gather）
        nba_s, mom_s, sm_s, llm_s, crowd_s = await asyncio.gather(
            nba_technical.evaluate(target),
            market_momentum.evaluate(target),
            smart_money.evaluate(target),
            sentiment_llm.evaluate(target) if phase >= PHASE_2 else NULL_SCORE,
            crowd_volume.evaluate(target),
        )

        # 4. 2 层融合 + 决策（含 ρ 动态调制 + 4 约束 bet sizing）
        rational  = state.weights.α1*nba_s + state.weights.α2*mom_s + state.weights.α3*sm_s
        sentient  = state.weights.β1*llm_s + state.weights.β2*crowd_s
        score     = state.weights.W_R*rational + state.weights.W_S*sentient

        # ρ 动态调制：基础 ρ_learned + survival horizon pressure
        breath, bankroll, pressure = await chain.getSurvivalState()
        ρ_effective = min(1.0, state.weights.rho + pressure * 0.5)

        if edge_sufficient(target, score):
            desired_usd      = ρ_effective * kelly_optimal(score) * confidence * bankroll
            max_by_breath    = breath * MAX_BREATH_RISK_PCT[desperate_mode] / CONVERSION_RATE
            liquidity_cap    = await polymarket.depth_at_5pct_slippage(target.market_id)
            bet_size_usd     = min(desired_usd, max_by_breath, bankroll, liquidity_cap)
            decision         = Decision(BET, target, bet_size_usd, score, reason=...)
        else:
            decision = Decision(NO_BET, reason=f"No edge ≥ {MIN_EDGE_THRESHOLD}")

        # 5. 决策上链：BET 和 NO_BET 都消耗 BREATH（防躺平），都必须 log
        if decision.kind == BET:
            # 原子化：betId 生成 + recordBetPlaced + consumeAction(BET) 一次完成
            bet_id = derive_bet_id(decision, current_block_hash)
            await chain.recordBetDecisionAndConsume(
                bet_id=bet_id,
                market_id=target.market_id,
                intended_size_usd=bet_size_usd,
            )
            # 链上 commit 后才发 Polymarket 真实订单
            await execute_polymarket_order(decision, bet_id)
            outcome = await wait_for_resolution(bet_id)
        else:
            await chain.consumeAction(ActionType.NO_BET, reason=decision.reason)
            outcome = NULL_OUTCOME

        # 7. 复盘（LLM 生成「内心独白」）—— 对 BET 和 NO_BET 都做
        reflection = await reflection_engine.reflect(decision, outcome)
        await chain.consumeAction(ActionType.REFLECT)
        await chain.log_reflection(decision.id, reflection.ipfs_hash)
        await dashboard.push_consciousness(reflection.text)

        # 7. 权重更新（Phase 3 跳过）
        if not state.weights_frozen:
            state.weights = weight_updater.update(state.weights, decision, outcome)
            await chain.update_weights_hash(hash(state.weights))

        # 8. 被动能量消耗
        await chain.apply_passive_burn()

        # 9. memory_bank persistence (PRD §4.6, §8 PLAYBACK 模式, §5.1 NFT, §13 V2)
        #    Single source of truth for: dashboard playback, Tombstone NFT contents,
        #    sim replay (Track C), V2 lineage seed, postmortem forensics.
        narrative_line = await narrative.write_narrative(   # fail-fast → template
            tick=current_tick, decision=decision, outcome=outcome,
        )
        await memory_bank.write_tick(
            tick=current_tick,
            schema_version="1.0",
            signals={"α1": nba_s, "α2": mom_s, "α3": sm_s, "β1": llm_s, "β2": crowd_s},
            fusion={"rational": rational, "sentient": sentient, "score": score},
            decision=decision.as_dict(),
            weights=state.weights.as_dict(),
            reflection_ipfs=reflection.ipfs_hash if decision.kind == BET else None,
            outcome=outcome.as_dict() if outcome != NULL_OUTCOME else None,
            narrative=narrative_line,
        )
        # On agent_loop startup (process restart), restore from disk before chain hash:
        #   last_ticks = memory_bank.load_last_k_ticks(10)
        #   if last_ticks: state.restore_from(last_ticks[-1].weights)
        # On permadeath (kill() path), pin tarball + record CID for Tombstone:
        #   tarball = memory_bank.make_tarball()
        #   cid = await ipfs.pin_with_retry(tarball, max_retries=3)
        #   await chain.mint_tombstone(..., memoryBankCid=cid or "")
```

### 4.2 权重学习（核心 ML 部分）

**权重模型**：2 层结构 + 体量参数，共 6 个独立优化参数。
```
# 预测分数
Rational = α₁·NBA_Stat + α₂·Market_Momentum + α₃·Smart_Money
Sentient = β₁·LLM_Sentiment + β₂·Crowd_Volume
Score    = W_R · Rational + W_S · Sentient

# 下注体量
bet_size = ρ · kelly_optimal · confidence · energy_balance

约束：α₁+α₂+α₃=1，β₁+β₂=1，W_R+W_S=1，ρ ∈ [0, 1]
独立优化变量：(W_R, α₁, α₂, β₁, α₃ 子参数化, ρ) 共 6 维
初始：W_R=0.6, W_S=0.4；α₁=0.5, α₂=0.3, α₃=0.2；β₁=0.6, β₂=0.4；ρ=0.5
```

**安全护栏（硬编码，不可学）**：
- 单次实际下注 ≤ 30% 当前能量（绝境模式下放宽到 50%）
- 同时最多 3 持仓
- ρ ≤ 1（不超过 Kelly 最优，避免过度激进）

**学习方法**：**分层 softmax + 指数加权梯度下降**
- 每个约束组（α 组、β 组、W 组）独立用 softmax 重新参数化为无约束 logits
- 损失函数：bet 结算后的 log-loss（预测概率 vs 实际 0/1）
- 学习率 η 随训练样本数衰减；用 EMA 平滑权重轨迹避免抖动
- 实现：纯 NumPy，约 100–150 行

**Phase 1 阶段特殊处理**：
- β₁（LLM 情绪）整段冻结为 0，β 组退化为 β₂=1
- 训练只在 (W_R, α₁, α₂, α₃) 4 维空间进行
- 这就是 PRD 提到的「童年期 LLM OFF」在算法上的体现

**Phase 2 阶段「LLM 首次激活」具体动作**：
- β₁ 解冻并初始化为 0.5（β₂ 同步降到 0.5）
- 学习率 η 临时放大 2× 持续 100 个样本，加速校准
- Dashboard 上触发「感性引擎激活」动画

**Phase 1 训练循环**（伪代码）：
```python
for game in historical_games_sorted_by_time:
    feats = extract_features_pointintime(game)
    pred_prob = weighted_score(feats, current_weights, llm_off=True)
    loss = logloss(pred_prob, game.actual_outcome)
    grads = backprop_through_softmax(loss, current_weights)
    current_weights = update_with_ema(current_weights, grads, η)
    log_evolution_snapshot(current_weights)
```

**理由**：5 个参数 + 几千场训练样本对收敛足够（参数/样本比 ~1:1000），可解释性强。不上 PyTorch、不上深度 RL。

### 4.3 LLM Sentiment Engine（Phase 2/3）

**输入**：某场比赛前 N 小时内提到该队的 Reddit 帖子 + Tweet（最多 50 条）
**调用**：
- 用 Claude Sonnet 4.6
- 单次 prompt 包含所有文本，让它输出结构化 JSON：
  ```json
  {
    "home_team_sentiment": -0.3,   // [-1, 1]
    "away_team_sentiment": 0.5,
    "confidence": 0.7,             // 信号强度
    "key_themes": ["LeBron injury rumor", "team B momentum"],
    "reasoning": "..."             // 给思维流用
  }
  ```
- 缓存 prompt（5 分钟内同场比赛复用）

**关键设计**：`reasoning` 字段直接喂给 Dashboard 思维流。这是 Agent「读到了什么」的可视化。

### 4.4 Reflection Engine

每次结算后，LLM 复盘：
- 输入：决策记录 + 实际结果 + 当前权重
- 输出：自然语言复盘 + 建议权重调整方向
- 复盘文本作为 IPFS 内容存档，hash 上链
- Demo 时 Dashboard 把它一字一字打出来

### 4.5 Market Momentum Engine（α₂）

**目标**：从 Polymarket 盘口本身提取信号，不依赖 NBA 统计或舆论。

**特征**：
- `implied_prob_drift = current_yes_price - opening_yes_price`（开盘以来累计变化）
- `velocity_1h, velocity_4h`（最近 1h / 4h 价格变化速率）
- `depth_imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)`（资金压向哪边）
- `volume_acceleration`（成交量是加速还是衰减）
- `spread_tightness`（盘口质量代理）

**实现**：
- Phase 1 训练阶段：从 Polymarket GraphQL API 拉历史 market 的 orderbook 快照（按小时聚合）
- Phase 2/3 实时阶段：订阅 Polymarket WebSocket，本地维护滚动窗口
- 输出归一化到 [-1, 1] 的单一分数，正值偏 home win，负值偏 away win

**关键纪律**：盘口动量 ≠ 跟风。Agent 学到的可能是「盘口动量 + NBA 统计逆向时偏信 NBA」——这种反信号也是合理学习产物。

### 4.6 MemoryBank module（PRD §4.6 + §5.1 + §8 + §13 实现层）

**目的**：把 Agent 的每 tick 认知状态结构化外化到磁盘，作为 dashboard PLAYBACK 模式、Tombstone NFT、Track C sim replay、V2 lineage seed 的**共同数据底座**。

**文件布局**：

```
.agent_state/memory_bank/
├── identity.md                       # 稳定：Agent 自我描述，写一次
├── goal.json                         # 当前目标 + KPI 历史
├── ticks/
│   └── tick_<N>.json                 # 主记录：signals/fusion/decision/weights/
│                                     #   reflection_ipfs/outcome + schema_version
├── summary/
│   └── tick_<N>_brief.md             # narrative.py 写入的 1-2 句独白
├── reflections/
│   └── tick_<N>.md                   # IPFS reflection 文本的本地镜像（BET tick only）
├── observations/
│   └── tick_<N>.json                 # 原始 env 快照（chain state + market data）
└── postmortem/                       # 仅 permadeath 时填充
    ├── final_state.json
    ├── last_K_summaries.md
    └── root_cause_hypothesis.md
```

**Schema 版本**：每个 `tick_<N>.json` 顶层带 `schema_version` 字段。Reader 走 `agent/core/memory_bank_migrations.py` 中的 `MIGRATIONS` 表向新版统一。Live agents 跨 sprint 升级时不丢数据。

**写入语义**（atomic temp+rename）：每个文件先写 `.tmp` 后 rename，读者永远看不到 partial 写入。Disk full → log + 继续 tick（agent must survive 不可违反）。

**Module API**：

```python
class MemoryBank:
    def __init__(self, root: Path = ".agent_state/memory_bank/"): ...

    def write_tick(self, *, tick: int, schema_version: str, signals: dict,
                   fusion: dict, decision: dict, weights: dict,
                   reflection_ipfs: str | None, outcome: dict | None,
                   narrative: str) -> None: ...

    def load_last_k_ticks(self, k: int = 10) -> list[TickRecord]: ...

    def make_tarball(self) -> bytes: ...               # for IPFS pin at kill()

    def write_postmortem(self, *, cause: str, modifier: str,
                         final_state: dict) -> None: ...
```

**Cost 模型**：~2KB per tick × 960 ticks per 30-day life ≈ ~2MB 总。Pinata free tier 容纳几个 Agent 完整 lifecycle 都没问题。Rotation 推迟到实际需要时（hackathon scope 之外）。

**Module 拆分**（DRY + 可测试）：
- `agent/core/memory_bank.py` — 纯 I/O，无 LLM 依赖（可单测，无需 mock LLM）
- `agent/core/narrative.py` — 单独的 narrative writer（可单测，无需 mock filesystem）
- `agent/core/memory_bank_schema.json` — 共享 JSON Schema（agent 写 + sim 读 + dashboard 读 + V2 boot 读的同一份契约）

**集成点**：
- `agent_loop` 末尾 step 9 调用 `memory_bank.write_tick(...)` + `narrative.write_narrative(...)` —— 见 §4.1
- `lifecycle.kill()` 调用 `memory_bank.make_tarball()` + `ipfs.pin_with_retry(max_retries=3)` + `chain.mint_tombstone(..., memoryBankCid=cid or "")` —— degraded path 当 IPFS 失败
- `dashboard_bridge` 在 PLAYBACK 模式下 mount 本地 bundled snapshot（不走 IPFS）
- `sim/replay.py` 在 calibration cross-validation 中加载 memory_bank tarball

### 4.7 绝境觉醒机制（Desperate Mode）

**触发**：Phase 3 期间 `energyBalance / initialEnergy ≤ 0.10`，由 `EnergyController.applyPassiveBurn()` 在状态转移时调用 `PhaseManager.enterDesperateMode()`。

**合约层（PhaseManager 扩展）**：
```solidity
bool public desperateMode;

function enterDesperateMode() external onlyEnergyController {
    require(currentPhase == Phase.Adulthood, "Only in Phase 3");
    require(!desperateMode, "Already desperate");
    desperateMode = true;
    emit DesperateModeEntered(block.number, energyBalance);
}
```

**Python 侧 weight_updater 响应**：
```python
if state.desperate_mode:
    # 仅解锁感性引擎 + 风险偏好
    update_targets = ["β1", "β2", "ρ"]
    # 理性引擎 α₁/α₂/α₃/W_R/W_S 继续冻结
    learning_rate *= 2.0
    bet_size_cap = 0.50  # 从 30% 放宽到 50%
```

**Dashboard 视觉响应**：
- 顶层切红色基调（从冷蓝 → 警示红）
- 增加标签 "RISK PREFERENCE UNLOCKED"
- DualEngineMeter 上 β₁/β₂/ρ 三个 cell 高亮闪烁
- 思维流注入一段系统消息：「**Energy critical. Recalibrating sentient engine and risk appetite. The crowd's voice is louder now.**」

**叙事映射**：「理性的人在绝境也变成了赌徒（ρ 增大、β 重新学习），但他还知道自己原本相信什么数据（α 不变）」。

### 4.8 Smart Money Engine（α₃）

**目标**：跟踪 Polygon 链上历史上在 NBA market 长期赢钱的钱包，把他们的当前仓位作为信号。

**离线阶段（一次性，Week 1）**：
1. 用 RPC（Alchemy / QuickNode 免费 tier）扫 Polymarket CTF Exchange 合约的所有历史事件
2. 过滤 NBA 标签的 market（用 Polymarket subgraph 或 metadata API 反查）
3. 按钱包 aggregate：总 PnL、胜率、交易数、NBA 专精度（NBA market / 总市场比例）
4. 筛选 **top wallets**：
   - 至少 30 场 NBA 结算记录
   - 胜率 ≥ 60%
   - 净盈利 > $5,000
   - 取前 50–100 个 wallet 入「smart money 白名单」
5. 白名单存 `smart_money_wallets.json`，链下持久化

**在线阶段（Phase 2/3）**：
- 监控这些钱包对每个待开 NBA market 的开仓动作
- 信号化：`α₃_signal = (Σ smart_money 押 home 的金额) / (Σ smart_money 总押注金额) - 0.5`
- 范围 [-0.5, 0.5]，正值 = 聪明钱看好 home，负值看好 away
- 若该 market 上 smart money 总押注 < 阈值，信号置 0（避免噪声）

**为什么这个信号 Web3-native 卖点拉满**：
- 完全依赖 Polygon 链上数据，不可能在传统 Web2 数据源复现
- 「让 Agent 学会跟着聪明钱走」是评委一听就懂的故事
- 离线 ETL 可以提前做完，不影响主循环延迟

---

## 5. Dashboard 设计

### 5.1 技术架构

- **Next.js 14 App Router**（SSR 不需要，纯客户端就行）
- **状态管理**：Zustand（极简）
- **样式**：Tailwind + shadcn/ui（避免造轮子）
- **图表**：Recharts（折线图 / 热力图）+ Framer Motion（关键动画）
- **数据来源**：
  - WebSocket（Agent backend 实时推）：思维流、即时决策
  - 链上 event 订阅（viem `watchContractEvent`）：权威生死状态

### 5.2 路由结构

```
/                 # 主仪表盘（Phase 2/3 都用）
/genesis          # 项目介绍页（Demo 视频源素材）
/lineage          # Tombstone NFT 画廊（V2 用，先占位）
/api/ws           # WebSocket 端点（如果用 Next.js 自带）
```

### 5.3 组件清单

| 组件 | 内容 |
|---|---|
| **VitalsPanel** | 能量条、倒计时、Gas 燃烧速率、Phase 标识 |
| **ConsciousnessStream** | Agent 思维流（typewriter 效果） |
| **DualEngineMeter** | 2 层结构：顶层 W_R/W_S 条带 + 底层 α₁/α₂/α₃/β₁/β₂ 热力图（随训练动态变化） |
| **EvolutionCurve** | 累计胜率 + 权重轨迹（两条折线叠加） |
| **DecisionFeed** | 最近 N 次下注详情（可展开看 LLM reasoning） |
| **DeathWatch**（能量 <10% 触发全屏覆盖） | 大字倒计时 + Last Words typewriter + Tombstone mint 动画 |

### 5.4 数据契约（Agent → Dashboard）

WebSocket 消息类型（JSON）：
```typescript
type Message =
  | { type: 'vitals', energy: number, burnRate: number, phase: Phase }
  | { type: 'thought', text: string, ts: number }
  | { type: 'decision', decision: DecisionRecord }
  | { type: 'reflection', text: string, decisionId: string }
  | { type: 'weights_updated', W_R: number, W_S: number,
      alpha1: number, alpha2: number, alpha3: number,
      beta1: number, beta2: number, rho: number }
  | { type: 'llm_activated' }       // Phase 1→2 时 β₁ 解冻的特殊事件
  | { type: 'desperate_mode_entered', energyPct: number }  // 绝境觉醒触发
  | { type: 'terminal_lucidity_start' }
  | { type: 'last_words', text: string }
  | { type: 'death', tombstoneTokenId: string };
```

---

## 6. 数据 Pipeline（按 Phase）

### Phase 1（一次性，离线）

```
balldontlie API ─────┐
                     │
Polymarket GraphQL ──┤         feature_engineering
(历史 orderbook 快照)├────►   (point-in-time          ──► training_set.parquet
                     │       strict slicing)
Polygon RPC scan ────┤
(historical events)  │              │
                     │              ▼
Reddit Pushshift ────┘    smart_money_wallets.json
                          (top NBA wallets 白名单)
                                 │
                                 ▼
                       phase1_runner.py ──► weights_v0.json
                                       └──► evolution_curve.csv
```

**关键纪律**：所有特征必须有 `available_at` 时间戳，且 < `game_start_time`。这是防止 look-ahead bias 的工程约束。Smart money 钱包识别也必须用「截至当时」的 wallet 历史，不能用未来表现回退。

### Phase 2（实时，连续）

```
NBA Live (balldontlie) ──┐
Polymarket WebSocket    ──┤   (盘口 + orderbook 深度)
Polygon RPC subscribe   ──┤   (smart money 实时仓位变化)
Reddit (PRAW)           ──┼──► async ingestion ──► Agent 主循环
Twitter (Apify)         ──┘                          │
                                                     ▼
Claude API ◄────────────── sentiment + reflection
                                                     │
                                                     ▼
                                        L3 链 + WebSocket → Dashboard
```

### Phase 3（实时 + 真金）

与 Phase 2 相同，但：
- `polymarket.execute()` 调用真实下注 API
- `weight_updater` disabled
- 启动时注入 0.05 ETH（待确认）
- 死亡触发 Tombstone mint

---

## 7. 链部署细节（v3 — 三链平行，Orbit L3 留 v2 roadmap）

### v1 主路径：三链平行部署（Plan v3 后敲定）

| 链 | 角色 | RPC | Settlement | 类目奖金 |
|---|---|---|---|---|
| **Robinhood Chain testnet** | 主部署 | docs.chain.robinhood.com 提供 | Arbitrum Sepolia | RH Chain top-3 reserved spot |
| **Arbitrum Sepolia L2** | Hot fallback | 公开 endpoint | Ethereum Sepolia | AI Agentic Category |
| **Polygon Amoy testnet** | Polymarket-native | 公开 endpoint | Polygon | Polygon ecosystem 类目 |

**关键工程：**
- 三链共享**完全相同的 .sol 源码** + **完全相同的 ABI**
- Foundry `foundry.toml [rpc_endpoints]` + 单一 `script/Deploy.s.sol`
- 部署命令：`forge script Deploy.s.sol --rpc-url $RPC_X --broadcast`（$RPC_X 切三个）
- Per-RPC fail-safe：一条链 deploy 失败不阻塞其他两条
- Dashboard 通过 `?chain={rh,sepolia,polygon_amoy}` URL param + nav toggle 切换

### v1 BREATH 经济（合约层实现）

- BREATH = **Soulbound ERC-20**（OpenZeppelin ERC20 + `_update()` override，禁止 user-to-user transfer）
- EnergyController.burnForAction(agent, cost) 检查余额、_burn、balance==0 触发 kill()
- 每次 Agent 行动 `burnForAction` 调用 = "Agent 体验到 BREATH 在烧"
- 经济效果与 chain-native gas 等价（agent 视角无差别），但实现成本 0（不需要 ArbOS 配置）

### v2 roadmap：Production Orbit L3 + BREATH chain-level native gas

留待 hackathon 后：
- 选 RaaS（Conduit / Caldera / 自建 nitro），约 $50-$3000/月
- ArbOS 配置 BREATH 为 native gas token
- 引入 BREATH transferable variant（与 v1 soulbound 设计 deliberate 分歧 —— gas token 必须 transferable to validators）
- Agent 行动**真正烧链层 gas**，Permadeath 从合约层语义升级为链层语义

---

## 8. 三周实施时间表

### Week 1：地基 + Phase 1 + **机制校准**（Day 1–7）

三 Track 并行：
- **Track A (Chain)**：合约部署与开发
- **Track B (Data)**：数据 pipeline + 训练
- **Track C (Sim)**：Layer 2 经济机制校准

| Day | Track A (Chain) | Track B (Data) | Track C (Sim) |
|---|---|---|---|
| 1 | **三链平行部署**（RH Chain + Sepolia + Polygon Amoy）+ Foundry scaffold + 合约骨架（占位参数）| 项目骨架 + balldontlie 接入 | **economy.py + 3 个 archetype 策略** |
| 2 | EnergyController v3.1（含 EIP-712 + replay protection）+ Foundry 单测 | NBA 历史特征工程 + Reddit Pushshift | **runner.py 单次 lifetime 模拟 + market.py 数据回放** |
| 3 | PhaseManager + AgentLifecycle + DecisionLog | Polymarket 历史盘口 ETL | **sweeper.py + Latin Hypercube sampling** |
| 4 | TombstoneNFT + 集成测试 | **Polygon 链上扫描 + Smart Money 识别** | **🚨 CALIBRATION_REPORT.md 产出**（用结果重新部署合约） |
| 5 | **合约用校准参数重新部署** + 5 个引擎模块实装 + weight_updater 6 参数版 | 引擎单测 | sim 收尾 / 备用 |
| 6 | Phase 1 训练跑通 + Dashboard 骨架（双层权重 + BREATH/bankroll 双 bar） | training_set v1 完整 | — |
| 7 | Buffer / 集成测试 / 文档 | | |

**Week 1 末关卡**：
- ✅ Phase 1 训练用**校准后参数**跑通，6 个权重参数收敛
- ✅ Dashboard 骨架可展示 BREATH 经济 + 双层权重曲线
- ✅ `CALIBRATION_REPORT.md` 是 Demo 素材之一

### Week 2：实时 + Phase 2 上线（Day 8–14）

| Day | 任务 | 交付 |
|---|---|---|
| 8 | Polymarket 实时盘口接入（含 orderbook 深度）+ Polygon RPC 实时订阅（smart money 仓位）+ nba_live | Agent 能看到实时 market + 实时 smart money 动态 |
| 9 | LLM Sentiment Engine + Reflection Engine + β₁「LLM 首次激活」事件流 | 单测：能对真实比赛生成情绪分 |
| 10 | DecisionLog 合约 + Agent 主循环（5 信号融合版本测通） | 主循环可链上 log |
| 11 | **Phase 1 → Phase 2 切换 + Phase 2 正式上线** | **🚨 Hard Deadline 🚨** |
| 12 | Dashboard 完整版（含 ConsciousnessStream + 双层 DualEngineMeter 实时更新） | UI 完全可用 |
| 13 | Phase 2 数据持续累积 + Bug 修复 | 学徒日记开始累积 |
| 14 | Buffer | 周末 |

**Week 2 末关卡**：Phase 2 已上线运行，Dashboard 能 Live 看 Agent 决策。

### Week 3：Phase 3 + Demo（Day 15–21）

| Day | 任务 | 交付 |
|---|---|---|
| 15 | TombstoneNFT 合约 + Death Watch UI | 死亡机制可端到端跑 |
| 16 | **绝境觉醒**（PhaseManager.enterDesperateMode + 红色 UI 切换 + weight_updater desperate 分支）+ Last Words 生成 prompt 调优 | 三段下坠完整可演示 |
| 17 | Phase 2 → Phase 3 切换流程演练（在 testnet 上） | Phase 3 上线就绪 |
| 18 | **Phase 3 正式启动** + LIVE 监控 | Demo 录制窗口 |
| 19 | Demo 视频录制 + 关键瞬间截图 | 视频素材 |
| 20 | Demo 视频剪辑 + Pitch deck 制作 + 提交材料 | 提交包 |
| 21 | Buffer / 修改 / 提交 | 提交 |

---

## 9. 关键路径与依赖

**关键路径（cannot slip）**：
```
Orbit L3 部署 (D1)
  → 核心合约 (D2)
  → Phase 1 训练 (D5)
  → Polymarket 实时 (D8)
  → Phase 2 上线 (D11) 🚨
  → Phase 3 准备 (D17)
  → Demo 录制 (D19)
```

**可并行**：
- Dashboard 开发与 Agent backend 开发可并行（约定 WebSocket schema 即可）
- 智能合约开发与数据 pipeline 可并行
- 训练 pipeline 与实时接入可并行

**可砍弃顺序**（若进度落后）：
1. ExternalBettingMarket 合约（最先砍）
2. Tombstone NFT 的 on-chain SVG 渲染（改为链下 JSON metadata）
3. Twitter 实时（保留 Reddit 即可）
4. 多 LLM 模型（Claude Sonnet 一个 fallback 即可）
5. 绝境觉醒机制（待你拍板，若不做则节省 2 天）

---

## 10. 风险登记册

| # | 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|---|
| 1 | ~~Orbit RaaS 部署遇阻~~ → **RaaS L3 成本超预算** | **已发生** | 高 | **v3 plan 解决：放弃 Orbit L3（v2 roadmap），改三链平行（RH Chain + Sepolia + Polygon Amoy 都 free）** |
| 1b | RH Chain testnet demo 当晚宕机 | 低-中 | 极高 | Sepolia hot fallback（Day 1 平行部署）+ dashboard chain-toggle 1-click 切换 |
| 1c | Death Watch WebSocket 断线静默失败 | 低-中 | 极高 | WS + 2s polling fallback（双数据源 + 取较新） |
| 1d | 框架 patching 消耗全部 dev 周期 | 高（已发生） | 极高 | **Framework freeze policy**：1hr/day patching 上限，仅 Tier 1 product-blocking 修；其余 advisor-veto via Level 3 hard wall 或 defer to 后 hackathon 框架债务 sprint |
| 2 | Polymarket API 限速 / 不稳 | 中 | 中 | 缓存层 + 备选 Kalshi |
| 3 | LLM 情绪信号噪声大导致 Phase 2 学不出东西 | 中 | 中 | 准备「人工干预剧本」，必要时手动注入有意义的决策点用于 Demo |
| 4 | Phase 2 deadline 滑掉 | 中 | 极高 | 每日同步进度，Day 9 若仍未跑通主循环就开始砍可选模块 |
| 5 | 实时下注延迟导致 Polymarket 盘口已变 | 高 | 中 | 加 slippage tolerance，重要决策预先 commit |
| 6 | 真金 Phase 3 早死无法 Demo | 中 | 中 | Demo 设计上不依赖「Agent 存活」，死亡本身就是高潮 |
| 7 | Demo 视频录制失败 / 关键瞬间错过 | 低 | 高 | 全程录屏 + 关键事件自动截图 |

---

## 11. 测试策略

**不做全 TDD**（黑客松不值得），但保证：

| 模块 | 测试类型 |
|---|---|
| Smart Contracts | Foundry 单测（生死状态机、能量边界、Phase 切换权限） |
| Phase 1 训练 pipeline | 端到端冒烟测试（一次跑通即可） |
| Agent 主循环 | Mock 数据下的端到端集成测试（最重要） |
| Dashboard | 手测（黑客松不写前端单测） |
| 数据 ingestion | 简单断言（schema 正确） |

---

## 12. 给 Demo 的「保险动作」

为防止 Phase 3 LIVE 时翻车，**所有保险机制都不能侵害 Permadeath trustless 叙事**：

1. **预录备份**：Phase 2 期间录下 1–2 段「学徒日记」精彩片段，剪进 Demo 视频
2. **死亡时机对齐**：通过校准初始注入量 + Phase 3 启动时机，让预期死亡时间落在评审窗口附近（这不是作弊，是参数合理选择 —— 写进 §14 Calibration objectives）
3. **副 dashboard**：本地另存一个静态版 dashboard，万一线上挂了能切
4. **EnergyController 的 Pausable 仅限 Phase 1/2**：合约 Pausable modifier 仅在 Phase Childhood/Apprenticeship 期间可用。**Phase 3 启动时自动 renounce pause 权限**（admin role 转 burn 地址），此后任何「冻结 Agent」操作都不可能。代码：

   ```solidity
   function transitionToAdulthood(...) external onlyAdmin {
       _renouncePauseRole();              // 不可逆放弃 pause
       _renounceUpgradeRole();            // 不可逆放弃合约升级
       currentPhase = Phase.Adulthood;
   }
   ```

> **关键叙事**：Pause 权限是 dev/testnet 阶段的安全开关，**Demo 期间项目方也无法救它**。这是 trustless permadeath 的核心。Demo 视频应该明确演示「按下 Phase 3 启动按钮 → 同时看到 pause/upgrade role 被 burn 的交易上链」 —— 给评委确认权限放弃的视觉证据。

---

## 13. 决策记录（v0.3，2026-05-15）

### 生存机制（v3.1 锁定）
完整设计见 PRD §6 与本文档 §3.1 / §3.7。要点：
- BREATH 单余额 + cumulative 归因分账
- 双账户（BREATH on L3 / USDC on Polygon）via EIP-712 self-oracle
- 对称 P&L 转换（200 BREATH/$）+ 4 约束 bet sizing
- 三类消耗（passive/action/idle，仅 BET 重置 idle）
- 三段下坠 + Apprenticeship Failure（P2 reset）+ Starvation Mode
- Death Cause 优先级 TradingLoss > Starvation > Attrition
- Donation 分账 + hourly cap + Terminal 拒收
- EIP-712 + replay protection + 标准 hygiene 全套

### 校准框架（Track C）
Day 1–4 跑 Monte Carlo（3 archetype × 200 lifetimes × LHS + BayesOpt），输出 CALIBRATION_REPORT.md 后才用于合约部署。

---

## 14. 历史决策（v0.2，已被 v0.3 覆盖；保留作为变更追溯）

- Q1: 下注体量改为可学参数 ρ → v0.3 进一步升级为 4 约束 min() 公式（含 bankroll/liquidity）
- Q2: 绝境觉醒 YES → v0.3 改为 pressure-based 触发（持续 2 cycles 防抖）
- Q3: 外部押注层延后 V2/Stretch → v0.3 维持

---

## 15. Open Engineering Gaps（工程缺口登记册）

设计阶段收尾时发现的 7 个工程缺口，逐项落档跟踪。状态：✅ 已决 / ⏳ Day 1 处理 / 📋 Week 1 内处理。

### Gap 1：Polymarket 真金下注实现 ⏳

**问题**：之前所有讨论都假设「Agent 在 Polymarket 下注」，但从未具体写过 Agent 如何**发送下单交易**。

**实际工程**：
- Polymarket 采用 CLOB（Central Limit Order Book）+ EIP-712 签名订单
- 官方 SDK：[`py-clob-client`](https://github.com/Polymarket/py-clob-client)
- Agent 工作流：
  1. 维护 Polygon 钱包（USDC + 少量 MATIC）
  2. 一次性 deposit USDC 到 Polymarket 的 collateral 合约
  3. 每次下注：构造订单 → EIP-712 签名 → POST 到 Polymarket CLOB
  4. 监听订单 fill 状态（partial / filled / cancelled）
  5. Market resolve 时拉 settlement 数据 → 通过 `settleBet()` 上链到 L3

**新增文件**：`code/agent/data/polymarket_executor.py`

**Day 1 行动**：先用 Polymarket 的 **Amoy testnet**（Polygon Amoy = Polygon testnet）跑通单笔下单流程。Track B 任务。

**风险**：Polymarket CLOB API 可能限速 / 文档不全 / testnet 行为与主网不一致。备选：直接走 mainnet 小额（团队预算允许）。

---

### Gap 2：Attestation Key 安全模型 ✅ 已决

**问题**：`attestationSigner` 私钥泄露 = 攻击者无限 mint BREATH。

**决定**：V1 用 **本地加密文件**（最简）方案：
- 私钥用强密码加密后存 `code/agent/.secrets/attestation_key.enc`
- 仅 Demo 服务器持有，文件权限 600
- Demo 中诚实说明：「V1 trust assumption. V2 uses LayerZero/Hyperlane native cross-chain messaging.」

**V2 升级路径**：用真桥取代 self-oracle，去除信任假设。

---

### Gap 3：Phase 3 真金来源 ✅ 已决

**决定**：团队预算覆盖。
- 初始 bankroll：**$50 USDC on Polygon**
- Gas：**$5 MATIC on Polygon**
- Orbit L3 native gas：免费 testnet
- **合计 ~$55**，由团队预算支付

**操作**：Week 3 Day 17 前完成钱包注资。

---

### Gap 4：Agent 进程崩溃恢复 📋

**问题**：Python 主循环挂了如何恢复？

**方案**：
1. **Supervisor**：用 systemd（如果 Linux VPS）或 PM2（如果用 Node 风格）做自动重启
2. **State recovery**：启动时从链上读最新状态（`breath`, `lastBetTimestamp`, `desperateMode`, etc.）→ 重建内存状态
3. **In-flight bets**：启动时扫描 `knownBetIds` 中未 settled 的，调 Polymarket 查询状态

**实现**：`code/agent/chain/state_sync.py`（Day 5–6）

---

### Gap 5：LLM 模型策略 + 结构化输出 ✅ 已决

**决定**：智能分级。

| 调用场景 | 模型 | 频率 |
|---|---|---|
| Sentiment scoring | claude-sonnet-4-6 | 每 cycle 1 次 |
| Thought stream | claude-sonnet-4-6 | 每 cycle 1 次 |
| 普通 Reflection | claude-sonnet-4-6 | 0.5–0.7/cycle |
| Phase 1→2 / 2→3 graduation 反思 | **claude-opus-4-7** | 一次性 |
| Desperate Mode 进入 / 连续 3 亏复盘 | **claude-opus-4-7** | 0–2/生 |
| Last Words 生成 | **claude-opus-4-7** | 一生一次 |

**结构化输出**：
- 用 Anthropic SDK 的 `tool_use` 模式强制 JSON schema
- 客户端用 Pydantic models 校验
- malformed → 自动 retry 1 次 → 仍失败降级默认值

**预算**：Phase 2 + Phase 3 合计 LLM 成本约 **$15–25 USD**。

**为何不用 Gemini 3.1 Pro**：双 SDK / 双 billing / 双错误路径成本 > 单模型潜在质量增益。Claude Sonnet 在我们的任务上已经够。

---

### Gap 6：校准框架的真实性验证 📋

**问题**：Layer 2 模拟得出的参数怎么保证在真实 Phase 3 也合理？

**方案：Backtest Validation**（Day 4 校准完成后追加）：
- 用 2024–2025 真实 NBA 数据 + Polymarket 真实历史盘口 replay Phase 3
- 检查 Agent 寿命分布是否仍落在 GOOD_CALIBRATION 范围
- 若不在 → 校准参数 + 模型不匹配，需重新调（约 0.5 天 buffer）

**实现**：`code/sim/backtest_validator.py`（Day 4 末）

---

### Gap 7：Pre-Demo Staging 演练 📋

**问题**：Demo 前必须有完整端到端「彩排链」运行。

**方案**：Week 3 Day 17 升级为正式 staging milestone：
- 在 testnet 部署一套**与 Demo 完全相同的合约**
- 跑完整 Phase 2→3 切换 + 6 小时 Phase 3 实盘演练
- 验证：所有合约调用 / Dashboard 实时更新 / Death Watch UI / Tombstone mint 链路 / Pitch deck 截图素材采集

**通过标准**：
- 演练期间 Agent 至少触发过一次 Desperate Mode + 一次 Lung Expansion + 几次 settlement
- Dashboard 全程无 disconnect
- **Phase 3 启动 tx 同时 emit `PauseRoleRenounced` 和 `UpgradeRoleRenounced` 事件**（Etherscan 可验证），作为 Demo 视频里「项目方放弃所有救命权限」的视觉证据

---

### Agent 运行架构补充（与本章节相关）

**Hybrid 持续服务器 + 内部 45min 心跳**：
- 进程 24/7 运行（VPS / Railway / Fly.io）
- 内部 asyncio scheduler 触发 45 分钟决策周期
- Polymarket WebSocket + Polygon RPC + Dashboard WS 持续订阅但**不触发 LLM**
- LLM 仅在 decision cycle 调用（约 80 次/天）
- 服务器成本：免费 tier 或 ~$5/月

---

*Draft v0.3 — 2026-05-15*
