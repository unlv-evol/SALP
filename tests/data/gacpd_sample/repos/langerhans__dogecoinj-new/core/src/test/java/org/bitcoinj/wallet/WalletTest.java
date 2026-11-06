        assertEquals(12, wallet.getKeyChainSeed().getMnemonicCode().size());
                .addChain(DeterministicKeyChain.builder().seed(new DeterministicSeed(ENTROPY, "", 1389353062L))
    public void roundtripViaMnemonicCode() {
        List<String> mnemonicCode = wallet.getKeyChainSeed().getMnemonicCode();
        final DeterministicSeed clonedSeed = new DeterministicSeed(mnemonicCode, null, "",
